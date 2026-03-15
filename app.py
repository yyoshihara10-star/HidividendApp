import streamlit as st
import pandas as pd
import yfinance as yf
import time
import random

st.set_page_config(page_title="プライム高配当株・王道銘柄完全捕捉", layout="wide")
st.title("高配当株スクリーニング (プライム全業種・全銘柄総当たり版)")

if 'result_df' not in st.session_state:
    st.session_state['result_df'] = pd.DataFrame()
if 'debug_log' not in st.session_state:
    st.session_state['debug_log'] = []

SOGO_SHOSHA_CODES = {8058, 8031, 8001, 8053, 8002, 8015, 2768}

# ── レート制限対策 ──────────────────────────────────
REQUEST_INTERVAL = 1.0   # リクエスト間の基本待機（秒）
MAX_RETRY        = 3     # 429時の最大リトライ回数
SECTOR_TOP_N     = 20    # 業種内上位何社をスクリーニングするか（API呼び出しなし絞り込み）
# ────────────────────────────────────────────────────

@st.cache_data(ttl=86400)
def fetch_jpx_prime():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        df = pd.read_excel(url, header=0)
        return df
    except:
        return pd.DataFrame()

def detect_columns(df):
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
        elif '規模' in c or 'サイズ' in c:
            col_map['size'] = col   # 規模区分（大型・中型・小型）があれば使う
    return col_map

def check_payout_recovery(info):
    trailing_eps = info.get('trailingEps')
    forward_eps  = info.get('forwardEps')
    if (trailing_eps is not None and forward_eps is not None
            and trailing_eps != 0 and forward_eps > trailing_eps):
        pct = round((forward_eps - trailing_eps) / abs(trailing_eps) * 100, 1)
        return True, f"業績回復見込み(予EPS+{pct}%)"
    rec = (info.get('recommendationKey') or '').lower()
    if rec in ('buy', 'strong_buy'):
        return True, "業績回復見込み(アナリスト買い推奨)"
    return False, ""

def fetch_with_retry(symbol):
    """1社分のデータ取得。429時は指数バックオフでリトライ。"""
    for attempt in range(MAX_RETRY):
        try:
            # リクエスト前に必ず待機
            wait = REQUEST_INTERVAL + random.uniform(0.1, 0.5)
            time.sleep(wait)

            ticker = yf.Ticker(symbol)
            info   = ticker.info

            if not info or len(info) < 5:
                return None, None, f"{symbol}: データ空"

            return ticker, info, None

        except Exception as e:
            msg = str(e)
            if 'Too Many Requests' in msg or '429' in msg:
                if attempt < MAX_RETRY - 1:
                    backoff = (2 ** attempt) * 8   # 8秒 → 16秒 → 32秒
                    time.sleep(backoff)
                else:
                    return None, None, f"{symbol}: レート制限（リトライ{MAX_RETRY}回失敗）"
            else:
                return None, None, f"{symbol}: 例外 {msg[:60]}"

    return None, None, f"{symbol}: リトライ上限超過"

def analyze_stock(symbol, industry, forced=False):
    ticker, info, err = fetch_with_retry(symbol)
    if info is None:
        return None, err

    price    = info.get('currentPrice') or info.get('previousClose') or 0
    if price == 0:
        return None, f"{symbol}: 株価なし"

    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate') or 0
    dy = round(div_rate / price * 100, 2)

    if not forced and dy < 3.0:
        return None, f"{symbol}: 利回り{dy}%（3%未満）"

    score   = 5
    reasons = []

    if forced and dy < 3.0:
        score -= 1
        reasons.append(f"利回り{dy}%（低め）")

    # 売上/利益トレンド
    try:
        fins     = ticker.financials
        rev_keys = ['Total Revenue', 'Operating Revenue', 'Revenue', 'Operating Income']
        found    = False
        for k in rev_keys:
            if k in fins.index:
                vals = pd.to_numeric(fins.loc[k], errors='coerce').dropna().values[:3]
                if len(vals) >= 2 and vals[0] < vals[1]:
                    score -= 1
                    reasons.append("売上/利益減")
                found = True
                break
        if not found:
            reasons.append("成長データ不明")
    except:
        reasons.append("成長データ取得失敗")

    # 配当性向
    payout = info.get('payoutRatio') or 0
    if 0 < payout <= 1.0:
        payout *= 100
    if payout > 70:
        ok, note = check_payout_recovery(info)
        if not ok:
            return None, f"{symbol}: 配当性向{round(payout)}%超・回復見込みなし→除外"
        score -= 1
        reasons.append(f"配当性向{round(payout)}%（一時的）")
        reasons.append(note)
    elif 0 < payout < 30:
        score -= 1
        reasons.append(f"配当性向{round(payout)}%（低）")
    elif payout == 0:
        reasons.append("性向データなし")

    # 自己資本比率
    eq_ratio   = 0.0
    is_finance = any(x in industry for x in ['銀行', '保険', '証券', 'その他金融'])
    try:
        bs = ticker.balance_sheet
        if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
            eq_ratio = round(
                bs.loc['Stockholders Equity'].iloc[0] /
                bs.loc['Total Assets'].iloc[0] * 100, 1
            )
            if not is_finance and eq_ratio < 40:
                score -= 1
                reasons.append(f"自己資本{eq_ratio}%")
    except:
        pass

    m_cap     = info.get('marketCap') or 0
    star      = max(1, score)
    judge     = "〇" if star >= 4 else ("△" if star >= 2 else "×")

    return {
        '利回り(%)':    dy,
        '配当性向(%)':  round(payout, 1),
        '自己資本(%)':  eq_ratio,
        '時価総額(億)': round(m_cap / 1_0000_0000) if m_cap else 0,
        '判定':         judge,
        'おすすめ度':   "★" * star + "☆" * (5 - star),
        '備考':         " / ".join(reasons) if reasons else "良好（指標クリア）",
        'score':        star,
        'm_cap':        m_cap,
    }, None

def sort_rows_by_jpx_priority(rows_df, col_map):
    """
    APIを使わずJPXデータだけで優先順位をつける。
    規模区分（大型>中型>小型）→ コード番号昇順（古い=歴史ある会社）
    """
    df = rows_df.copy()
    if 'size' in col_map and col_map['size'] in df.columns:
        size_order = {'大型株': 0, '中型株': 1, '小型株': 2}
        df['_size_rank'] = df[col_map['size']].map(size_order).fillna(3)
        df = df.sort_values(['_size_rank', col_map['code']])
    else:
        # 規模列がなければコード昇順（古い大企業が小さい番号を持つ傾向）
        df = df.sort_values(col_map['code'])
    return df

def scan_rows(rows_df, industry, col_map, top_n, forced=False):
    """上位top_n社だけAPIを叩いてスクリーニング"""
    sorted_df  = sort_rows_by_jpx_priority(rows_df, col_map)
    target_df  = sorted_df.head(top_n)
    candidates = []
    skip_log   = []

    for _, row in target_df.iterrows():
        code = f"{int(row[col_map['code']])}.T"
        name = row[col_map['name']]
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
    st.session_state['result_df'] = pd.DataFrame()

    raw_df = fetch_jpx_prime()
    if raw_df.empty:
        st.error("JPXデータ取得失敗")
        st.stop()

    col_map = detect_columns(raw_df)
    missing = [k for k in ['market', 'industry', 'code', 'name'] if k not in col_map]
    with st.expander("🔍 JPXデータ確認", expanded=bool(missing)):
        st.write("列マッピング:", col_map)
        st.dataframe(raw_df.head(3))
    if missing:
        st.error(f"列が見つかりません: {missing}")
        st.stop()

    jpx_df = raw_df[raw_df[col_map['market']].astype(str).str.contains('プライム')].copy()
    jpx_df[col_map['code']] = pd.to_numeric(jpx_df[col_map['code']], errors='coerce')
    jpx_df = jpx_df.dropna(subset=[col_map['code']])
    st.info(f"プライム銘柄数: {len(jpx_df)} 件")

    shosha_rows   = jpx_df[jpx_df[col_map['code']].isin(SOGO_SHOSHA_CODES)]
    non_shosha_df = jpx_df[~jpx_df[col_map['code']].isin(SOGO_SHOSHA_CODES)]
    all_industries = sorted(non_shosha_df[col_map['industry']].dropna().unique())

    final_results = []
    all_skip_log  = []
    status_text   = st.empty()
    progress_bar  = st.progress(0)
    total_steps   = len(all_industries) + 1

    for idx, industry in enumerate(all_industries):
        sector_df = non_shosha_df[non_shosha_df[col_map['industry']] == industry]
        n = min(SECTOR_TOP_N, len(sector_df))
        status_text.text(f"[{idx+1}/{len(all_industries)}] {industry}（上位{n}社をスキャン中...）")

        candidates, skips = scan_rows(sector_df, industry, col_map, top_n=SECTOR_TOP_N, forced=False)
        all_skip_log.extend(skips)

        # 1件も取れなければ強制選出
        if not candidates:
            status_text.text(f"[{idx+1}/{len(all_industries)}] {industry}（強制選出中...）")
            candidates, skips2 = scan_rows(sector_df, industry, col_map, top_n=SECTOR_TOP_N, forced=True)
            all_skip_log.extend(skips2)

        if candidates:
            sorted_c = sorted(candidates, key=lambda x: (x['score'], x['m_cap']), reverse=True)
            final_results.extend(sorted_c[:5])
        else:
            all_skip_log.append(f"⚠️ {industry}: 強制選出でも0件")

        progress_bar.progress((idx + 1) / total_steps)

    # 商社スキャン
    status_text.text(f"商社（総合商社{len(shosha_rows)}社をスキャン中...）")
    shosha_cand, skips = scan_rows(shosha_rows, "商社", col_map, top_n=10, forced=False)
    all_skip_log.extend(skips)
    if not shosha_cand:
        shosha_cand, skips2 = scan_rows(shosha_rows, "商社", col_map, top_n=10, forced=True)
        all_skip_log.extend(skips2)
    if shosha_cand:
        final_results.extend(sorted(shosha_cand, key=lambda x: (x['score'], x['m_cap']), reverse=True))

    progress_bar.progress(1.0)
    status_text.text("✅ スキャン完了！")
    st.session_state['debug_log'] = all_skip_log

    if final_results:
        cols = ['業種', 'コード', '銘柄名', '利回り(%)', '配当性向(%)',
                '自己資本(%)', '時価総額(億)', '判定', 'おすすめ度', '備考']
        st.session_state['result_df'] = pd.DataFrame(final_results)[cols]
        st.success(f"✅ {len(final_results)} 銘柄を検出")
    else:
        st.error("❌ 結果0件。スキップログを確認してください。")

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
            st.dataframe(df_show[df_show['業種'] == ind].reset_index(drop=True),
                         use_container_width=True)

    csv = df_show.to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 CSVダウンロード", csv, "prime_high_dividend.csv", "text/csv")

if st.session_state['debug_log']:
    with st.expander(f"🔎 スキップログ ({len(st.session_state['debug_log'])}件)", expanded=False):
        for line in st.session_state['debug_log']:
            st.text(line)
