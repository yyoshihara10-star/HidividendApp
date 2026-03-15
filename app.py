import streamlit as st
import pandas as pd
import yfinance as yf

st.set_page_config(page_title="プライム高配当株・王道銘柄完全捕捉", layout="wide")
st.title("高配当株スクリーニング (プライム全業種・全銘柄総当たり版)")

if 'result_df' not in st.session_state:
    st.session_state['result_df'] = pd.DataFrame()
if 'debug_log' not in st.session_state:
    st.session_state['debug_log'] = []

SOGO_SHOSHA_CODES = {8058, 8031, 8001, 8053, 8002, 8015, 2768}

@st.cache_data(ttl=86400)
def fetch_jpx_prime():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        df = pd.read_excel(url, header=0)
        return df
    except Exception as e:
        return pd.DataFrame(), str(e)

def detect_columns(df):
    """列名を自動検出して正規化する"""
    col_map = {}
    for col in df.columns:
        c = str(col).strip()
        if '市場' in c or '商品区分' in c:
            col_map['market'] = col
        elif '33業種' in c or '業種区分' in c:
            col_map['industry'] = col
        elif 'コード' in c or 'code' in c.lower():
            col_map['code'] = col
        elif '銘柄' in c or '名称' in c:
            col_map['name'] = col
    return col_map

def check_payout_recovery(info):
    trailing_eps = info.get('trailingEps')
    forward_eps  = info.get('forwardEps')
    if (trailing_eps is not None and forward_eps is not None
            and trailing_eps != 0 and forward_eps > trailing_eps):
        improvement = round((forward_eps - trailing_eps) / abs(trailing_eps) * 100, 1)
        return True, f"業績回復見込み(予EPS+{improvement}%)"
    rec = (info.get('recommendationKey') or '').lower()
    if rec in ('buy', 'strong_buy'):
        return True, "業績回復見込み(アナリスト買い推奨)"
    return False, ""

def score_stock(info, financials, balance_sheet, industry, dy, forced=False):
    """スコアリングのコアロジック。スコアとresを返す。除外の場合はNone。"""
    score   = 5
    reasons = []

    if forced and dy < 3.0:
        score -= 1
        reasons.append(f"利回り{dy}%（低め）")

    # 売上/利益トレンド
    rev_keys = ['Total Revenue', 'Operating Revenue', 'Revenue', 'Operating Income']
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

    # 配当性向
    payout = info.get('payoutRatio', 0) or 0
    if 0 < payout <= 1.0:
        payout *= 100
    if payout > 70:
        is_recovery, recovery_note = check_payout_recovery(info)
        if not is_recovery:
            return None, None   # 除外
        score -= 1
        reasons.append(f"配当性向{round(payout)}%（一時的）")
        reasons.append(recovery_note)
    elif 0 < payout < 30:
        score -= 1
        reasons.append(f"配当性向{round(payout)}%（低）")
    elif payout == 0:
        reasons.append("性向データなし")

    # 自己資本比率
    eq_ratio   = 0.0
    is_finance = any(x in industry for x in ['銀行', '保険', '証券', 'その他金融'])
    try:
        if 'Stockholders Equity' in balance_sheet.index and 'Total Assets' in balance_sheet.index:
            eq_ratio = round(
                balance_sheet.loc['Stockholders Equity'].iloc[0] /
                balance_sheet.loc['Total Assets'].iloc[0] * 100, 1
            )
            if not is_finance and eq_ratio < 40:
                score -= 1
                reasons.append(f"自己資本{eq_ratio}%")
    except:
        pass

    m_cap     = info.get('marketCap', 0) or 0
    star      = max(1, score)
    judge     = "〇" if star >= 4 else ("△" if star >= 2 else "×")
    m_cap_oku = round(m_cap / 1_000_000_00) if m_cap else 0

    res = {
        '利回り(%)':    dy,
        '配当性向(%)':  round(payout, 1),
        '自己資本(%)':  eq_ratio,
        '時価総額(億)': m_cap_oku,
        '判定':         judge,
        'おすすめ度':   "★" * star + "☆" * (5 - star),
        '備考':         " / ".join(reasons) if reasons else "良好（指標クリア）",
        'score':        star,
        'm_cap':        m_cap,
    }
    return res, None

def analyze_stock(symbol, industry, forced=False):
    try:
        stock  = yf.Ticker(symbol)
        info   = stock.info

        # infoが空の場合はスキップ
        if not info or len(info) < 5:
            return None, f"{symbol}: infoデータ取得失敗"

        price    = info.get('currentPrice') or info.get('previousClose') or 0
        if price == 0:
            return None, f"{symbol}: 株価データなし"

        div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate') or 0
        dy = round((div_rate / price * 100), 2) if price > 0 else 0.0

        if not forced and dy < 3.0:
            return None, f"{symbol}: 利回り{dy}%（3%未満）"

        financials    = stock.financials
        balance_sheet = stock.balance_sheet

        res, _ = score_stock(info, financials, balance_sheet, industry, dy, forced)
        if res is None:
            return None, f"{symbol}: 配当性向70%超＆回復見込みなし"
        return res, None

    except Exception as e:
        return None, f"{symbol}: 例外 {str(e)[:80]}"

def scan_industry(rows, industry, col_map, forced=False):
    candidates = []
    skip_log   = []
    for _, row in rows.iterrows():
        code   = f"{int(row[col_map['code']])}.T"
        name   = row[col_map['name']]
        res, reason = analyze_stock(code, industry, forced=forced)
        if res:
            res.update({'業種': industry, 'コード': int(row[col_map['code']]), '銘柄名': name})
            candidates.append(res)
        elif reason:
            skip_log.append(reason)
    return candidates, skip_log

# ─── メインUI ────────────────────────────────────────
if st.button("🚀 プライム全銘柄・徹底スキャン", type="primary"):
    st.session_state['debug_log'] = []
    raw_df = fetch_jpx_prime()

    # fetch_jpx_prime がタプルを返すケースに対応
    if isinstance(raw_df, tuple):
        st.error(f"JPXデータ取得失敗: {raw_df[1]}")
        st.stop()
    if raw_df.empty:
        st.error("JPXデータが空です。")
        st.stop()

    # ── 列名自動検出 ────────────────────────────────
    col_map = detect_columns(raw_df)
    missing = [k for k in ['market','industry','code','name'] if k not in col_map]

    with st.expander("🔍 JPXデータ確認（列名デバッグ）", expanded=bool(missing)):
        st.write("検出された列:", list(raw_df.columns))
        st.write("列マッピング:", col_map)
        st.dataframe(raw_df.head(3))

    if missing:
        st.error(f"以下の列が見つかりません: {missing}。上のデバッグ情報を確認してください。")
        st.stop()

    # プライム市場フィルター
    jpx_df = raw_df[raw_df[col_map['market']].astype(str).str.contains('プライム')].copy()
    jpx_df[col_map['code']] = pd.to_numeric(jpx_df[col_map['code']], errors='coerce')
    jpx_df = jpx_df.dropna(subset=[col_map['code']])
    st.info(f"プライム銘柄数: {len(jpx_df)} 件")

    # 商社分離
    shosha_rows   = jpx_df[jpx_df[col_map['code']].isin(SOGO_SHOSHA_CODES)].copy()
    non_shosha_df = jpx_df[~jpx_df[col_map['code']].isin(SOGO_SHOSHA_CODES)].copy()
    all_industries = sorted(non_shosha_df[col_map['industry']].dropna().unique())

    final_results = []
    all_skip_log  = []
    status_text   = st.empty()
    progress_bar  = st.progress(0)
    total_steps   = len(all_industries) + 1

    for idx, industry in enumerate(all_industries):
        status_text.text(f"[{idx+1}/{len(all_industries)}] スキャン中: {industry}")
        sector_rows = non_shosha_df[non_shosha_df[col_map['industry']] == industry]

        # 通常スキャン
        candidates, skips = scan_industry(sector_rows, industry, col_map, forced=False)
        all_skip_log.extend(skips)

        # 1銘柄も取れなければ強制選出
        if not candidates:
            status_text.text(f"[{idx+1}/{len(all_industries)}] 強制選出中: {industry}")
            candidates, skips2 = scan_industry(sector_rows, industry, col_map, forced=True)
            all_skip_log.extend(skips2)

        if candidates:
            sorted_c = sorted(candidates, key=lambda x: (x['score'], x['m_cap']), reverse=True)
            final_results.extend(sorted_c[:5])
        else:
            all_skip_log.append(f"⚠️ {industry}: 強制選出でも0件")

        progress_bar.progress((idx + 1) / total_steps)

    # 商社スキャン
    status_text.text("スキャン中: 商社（総合商社）")
    shosha_candidates, skips = scan_industry(shosha_rows, "商社", col_map, forced=False)
    all_skip_log.extend(skips)
    if not shosha_candidates:
        shosha_candidates, skips2 = scan_industry(shosha_rows, "商社", col_map, forced=True)
        all_skip_log.extend(skips2)
    if shosha_candidates:
        final_results.extend(sorted(shosha_candidates, key=lambda x: (x['score'], x['m_cap']), reverse=True))

    progress_bar.progress(1.0)
    status_text.text("✅ スキャン完了！")
    st.session_state['debug_log'] = all_skip_log

    if final_results:
        cols = ['業種','コード','銘柄名','利回り(%)','配当性向(%)',
                '自己資本(%)','時価総額(億)','判定','おすすめ度','備考']
        st.session_state['result_df'] = pd.DataFrame(final_results)[cols]
        st.success(f"✅ {len(final_results)} 銘柄を検出")
    else:
        st.error("❌ 結果が0件です。下のデバッグログを確認してください。")

# ─── 結果表示 ────────────────────────────────────────
if not st.session_state['result_df'].empty:
    df_show    = st.session_state['result_df']
    industries = df_show['業種'].unique().tolist()
    if "商社" in industries:
        industries = ["商社"] + [i for i in industries if i != "商社"]

    tabs = st.tabs(["📋 全件"] + industries)
    with tabs[0]:
        st.dataframe(df_show, use_container_width=True)
    for tab, ind in zip(tabs[1:], industries):
        with tab:
            st.dataframe(df_show[df_show['業種'] == ind].reset_index(drop=True), use_container_width=True)

    csv = df_show.to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 CSVダウンロード", csv, "prime_high_dividend.csv", "text/csv")

# ─── デバッグログ（常時表示） ──────────────────────────
if st.session_state['debug_log']:
    with st.expander(f"🔎 スキップログ ({len(st.session_state['debug_log'])}件)", expanded=False):
        for line in st.session_state['debug_log']:
            st.text(line)
