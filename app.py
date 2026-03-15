import streamlit as st
import pandas as pd
import yfinance as yf

st.set_page_config(page_title="プライム高配当株・王道銘柄完全捕捉", layout="wide")
st.title("高配当株スクリーニング (プライム全業種・全銘柄総当たり版)")

if 'result_df' not in st.session_state:
    st.session_state['result_df'] = pd.DataFrame()

# ── 総合商社コード一覧（卸売業から独立させる） ─────────────────
SOGO_SHOSHA_CODES = {
    8058: "三菱商事",
    8031: "三井物産",
    8001: "伊藤忠商事",
    8053: "住友商事",
    8002: "丸紅",
    8015: "豊田通商",
    2768: "双日",
}
# ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=86400)
def fetch_jpx_prime():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        df = pd.read_excel(url)
        df = df[df['市場・商品区分'].str.contains('プライム')].copy()
        df['コード'] = pd.to_numeric(df['コード'], errors='coerce')
        return df
    except:
        return pd.DataFrame()

def check_payout_recovery(info):
    trailing_eps = info.get('trailingEps')
    forward_eps  = info.get('forwardEps')
    if (
        trailing_eps is not None and forward_eps is not None
        and trailing_eps != 0
        and forward_eps > trailing_eps
    ):
        improvement = round((forward_eps - trailing_eps) / abs(trailing_eps) * 100, 1)
        return True, f"業績回復見込み(予EPS+{improvement}%)"
    recommendation = (info.get('recommendationKey') or '').lower()
    if recommendation in ('buy', 'strong_buy'):
        return True, "業績回復見込み(アナリスト買い推奨)"
    return False, ""

def analyze_stock(symbol, industry):
    """
    スコアリングしてスクリーニング結果を返す。
    配当性向70%超で回復見込みなし → None を返す（除外）。
    利回り3%未満でも「強制選出モード」で拾えるよう dy は返す。
    """
    stock = yf.Ticker(symbol)
    info  = stock.info

    price    = info.get('currentPrice') or info.get('previousClose') or 1.0
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    dy = round((div_rate / price * 100), 2)
    if dy < 3.0:
        return None

    score   = 5
    reasons = []

    # 売上/利益トレンド
    financials   = stock.financials
    rev_keys     = ['Total Revenue', 'Operating Revenue', 'Revenue', 'Operating Income']
    growth_found = False
    for k in rev_keys:
        if k in financials.index:
            vals = pd.to_numeric(financials.loc[k], errors='coerce').dropna().values[:3]
            if len(vals) >= 2 and vals[0] < vals[1]:
                score -= 1
                reasons.append("売上/利益減")
            growth_found = True
            break
    if not growth_found:
        reasons.append("成長データ不明")

    # 配当性向チェック
    payout = info.get('payoutRatio', 0)
    if payout <= 1.0:
        payout *= 100
    if payout > 70:
        is_recovery, recovery_note = check_payout_recovery(info)
        if not is_recovery:
            return None   # 回復見込みなし → 除外
        score -= 1
        reasons.append(f"配当性向 {round(payout)}%（一時的）")
        reasons.append(recovery_note)
    elif payout < 30:
        score -= 1
        reasons.append(f"配当性向 {round(payout)}%（低）")

    # 自己資本比率
    bs = stock.balance_sheet
    eq_ratio   = 0.0
    is_finance = any(x in industry for x in ['銀行', '保険', '証券', 'その他金融'])
    if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
        try:
            eq_ratio = round(
                bs.loc['Stockholders Equity'].iloc[0] /
                bs.loc['Total Assets'].iloc[0] * 100, 1
            )
            if not is_finance and eq_ratio < 40:
                score -= 1
                reasons.append(f"自己資本 {eq_ratio}%")
        except:
            pass

    m_cap      = info.get('marketCap', 0)
    star_score = max(1, score)
    judge      = "〇" if star_score >= 4 else ("△" if star_score >= 2 else "×")
    m_cap_oku  = round(m_cap / 1_000_000_00) if m_cap else 0

    return {
        '利回り(%)':    dy,
        '配当性向(%)':  round(payout, 1),
        '自己資本(%)':  eq_ratio,
        '時価総額(億)': m_cap_oku,
        '判定':         judge,
        'おすすめ度':   "★" * star_score + "☆" * (5 - star_score),
        '備考':         " / ".join(reasons) if reasons else "良好（指標クリア）",
        'score':        star_score,
        'm_cap':        m_cap,
    }

def analyze_stock_forced(symbol, industry):
    """
    利回り3%未満でも強制的にスコアリングして返す（各業種1銘柄保証用）。
    配当性向70%超かつ回復見込みなし の場合のみ None。
    """
    stock = yf.Ticker(symbol)
    info  = stock.info

    price    = info.get('currentPrice') or info.get('previousClose') or 1.0
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    dy = round((div_rate / price * 100), 2)

    score   = 5
    reasons = []
    if dy < 3.0:
        score -= 1
        reasons.append(f"利回り {dy}%（低め）")

    financials   = stock.financials
    rev_keys     = ['Total Revenue', 'Operating Revenue', 'Revenue', 'Operating Income']
    growth_found = False
    for k in rev_keys:
        if k in financials.index:
            vals = pd.to_numeric(financials.loc[k], errors='coerce').dropna().values[:3]
            if len(vals) >= 2 and vals[0] < vals[1]:
                score -= 1
                reasons.append("売上/利益減")
            growth_found = True
            break
    if not growth_found:
        reasons.append("成長データ不明")

    payout = info.get('payoutRatio', 0)
    if payout <= 1.0:
        payout *= 100
    if payout > 70:
        is_recovery, recovery_note = check_payout_recovery(info)
        if not is_recovery:
            return None
        score -= 1
        reasons.append(f"配当性向 {round(payout)}%（一時的）")
        reasons.append(recovery_note)
    elif payout < 30:
        score -= 1
        reasons.append(f"配当性向 {round(payout)}%（低）")

    bs = stock.balance_sheet
    eq_ratio   = 0.0
    is_finance = any(x in industry for x in ['銀行', '保険', '証券', 'その他金融'])
    if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
        try:
            eq_ratio = round(
                bs.loc['Stockholders Equity'].iloc[0] /
                bs.loc['Total Assets'].iloc[0] * 100, 1
            )
            if not is_finance and eq_ratio < 40:
                score -= 1
                reasons.append(f"自己資本 {eq_ratio}%")
        except:
            pass

    m_cap      = info.get('marketCap', 0)
    star_score = max(1, score)
    judge      = "〇" if star_score >= 4 else ("△" if star_score >= 2 else "×")
    m_cap_oku  = round(m_cap / 1_000_000_00) if m_cap else 0

    return {
        '利回り(%)':    dy,
        '配当性向(%)':  round(payout, 1),
        '自己資本(%)':  eq_ratio,
        '時価総額(億)': m_cap_oku,
        '判定':         judge,
        'おすすめ度':   "★" * star_score + "☆" * (5 - star_score),
        '備考':         " / ".join(reasons) if reasons else "良好（指標クリア）",
        'score':        star_score,
        'm_cap':        m_cap,
    }

def scan_industry(rows, industry, status_text, forced=False):
    """
    業種の行リストを受け取り、候補リストを返す。
    forced=True の場合、利回り3%未満でも強制取得。
    """
    candidates = []
    for _, row in rows.iterrows():
        code = f"{row['コード']}.T"
        try:
            res = analyze_stock(code, industry)
            # 通常スキャンで取れなかった場合、強制モードで再取得
            if res is None and forced:
                res = analyze_stock_forced(code, industry)
            if res:
                res.update({
                    '業種':   industry,
                    'コード': row['コード'],
                    '銘柄名': row['銘柄名'],
                })
                candidates.append(res)
        except:
            continue
    return candidates

if st.button("🚀 プライム全銘柄・徹底スキャン", type="primary"):
    jpx_df = fetch_jpx_prime()
    if jpx_df.empty:
        st.error("JPXデータの取得に失敗しました。")
        st.stop()

    # ── 商社を卸売業から分離 ────────────────────────────
    shosha_rows    = jpx_df[jpx_df['コード'].isin(SOGO_SHOSHA_CODES.keys())].copy()
    non_shosha_df  = jpx_df[~jpx_df['コード'].isin(SOGO_SHOSHA_CODES.keys())].copy()
    all_industries = sorted(non_shosha_df['33業種区分'].unique())
    # ──────────────────────────────────────────────────

    final_results = []
    status_text   = st.empty()
    progress_bar  = st.progress(0)
    total_steps   = len(all_industries) + 1  # +1 for 商社

    # ── 通常業種スキャン ──────────────────────────────
    for idx, industry in enumerate(all_industries):
        status_text.text(f"【スキャン中】業種: {industry}  ({idx+1}/{len(all_industries)})")
        sector_rows = non_shosha_df[non_shosha_df['33業種区分'] == industry]

        # 通常スキャン（利回り3%以上）
        candidates = scan_industry(sector_rows, industry, status_text, forced=False)

        # 1銘柄も取れなかった場合 → 強制モードで最良1銘柄を確保
        if not candidates:
            status_text.text(f"【強制選出中】業種: {industry}（条件緩和）")
            candidates = scan_industry(sector_rows, industry, status_text, forced=True)

        if candidates:
            # ① まずスコア降順、② 同スコアなら時価総額降順でソート → 上位5社
            candidates_sorted = sorted(
                candidates,
                key=lambda x: (x['score'], x['m_cap']),
                reverse=True
            )
            final_results.extend(candidates_sorted[:5])

        progress_bar.progress((idx + 1) / total_steps)

    # ── 商社スキャン（独立業種） ───────────────────────
    status_text.text("【スキャン中】業種: 商社（総合商社）")
    shosha_candidates = scan_industry(shosha_rows, "商社", status_text, forced=False)
    if not shosha_candidates:
        shosha_candidates = scan_industry(shosha_rows, "商社", status_text, forced=True)
    if shosha_candidates:
        shosha_sorted = sorted(
            shosha_candidates,
            key=lambda x: (x['score'], x['m_cap']),
            reverse=True
        )
        final_results.extend(shosha_sorted)   # 商社は全件表示

    progress_bar.progress(1.0)
    status_text.text("✅ スキャン完了！")

    if final_results:
        cols = ['業種', 'コード', '銘柄名', '利回り(%)', '配当性向(%)',
                '自己資本(%)', '時価総額(億)', '判定', 'おすすめ度', '備考']
        st.session_state['result_df'] = pd.DataFrame(final_results)[cols]
        st.success(f"✅ {len(final_results)} 銘柄を検出")

if not st.session_state['result_df'].empty:
    df_show = st.session_state['result_df']

    # 業種ごとにタブ表示（見やすさ向上）
    industries = df_show['業種'].unique().tolist()
    # 商社タブを先頭に
    if "商社" in industries:
        industries = ["商社"] + [i for i in industries if i != "商社"]

    tabs = st.tabs(industries)
    for tab, ind in zip(tabs, industries):
        with tab:
            st.dataframe(
                df_show[df_show['業種'] == ind].reset_index(drop=True),
                use_container_width=True
            )

    st.divider()
    st.dataframe(df_show, use_container_width=True)
    csv = df_show.to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 CSVダウンロード", csv, "prime_high_dividend.csv", "text/csv")
