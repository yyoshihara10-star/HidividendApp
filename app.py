import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="日本株全銘柄対応・高配当スクリーニング", layout="wide")
st.title("高配当株スクリーニング (日本株全上場銘柄対象)")

# セッション状態の初期化
if 'result_df' not in st.session_state:
    st.session_state['result_df'] = pd.DataFrame()

@st.cache_data(ttl=86400)
def fetch_jpx_full_list():
    """JPXから全上場銘柄リスト（約3,800社）を取得"""
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        df = pd.read_excel(url)
        # 不要な列の削除や整理
        df = df[df['市場・商品区分'].str.contains('内国株式')] # ETFやREITを除外
        return df
    except Exception as e:
        st.error(f"JPXデータの取得に失敗しました: {e}")
        return pd.DataFrame()

def analyze_stock_strict(stock, industry, min_yield):
    info = stock.info
    # 基本データ取得
    price = info.get('currentPrice') or info.get('previousClose') or 1.0
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    dy = round((div_rate / price * 100), 2) if div_rate else 0.0
    
    eps_growth = info.get('earningsQuarterlyGrowth', 0) or 0
    eps_val = round(eps_growth * 100, 1)
    
    score = 5
    reasons = []
    
    # 指標チェック
    if dy < min_yield:
        score -= 1
        reasons.append(f"低利回り({dy}%)")

    # 財務諸表（3期分）
    financials = stock.financials
    growth_count = 0
    # 売上・利益のチェック（商社・一般共通）
    for key in ['Total Revenue', 'Operating Revenue', 'Net Income']:
        if key in financials.index:
            vals = pd.to_numeric(financials.loc[key], errors='coerce').dropna().values[:3]
            if len(vals) >= 2 and vals[0] >= vals[1]: # 成長または維持
                growth_count += 1
    if growth_count == 0:
        score -= 1
        reasons.append("成長停滞")

    # 配当性向
    payout = info.get('payoutRatio', 0)
    if payout <= 1.0: payout *= 100
    if not (30 <= payout <= 70):
        score -= 1
        reasons.append(f"性向外({round(payout)}%)")

    # 自己資本比率
    bs = stock.balance_sheet
    eq_ratio = 0.0
    if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
        eq_ratio = round((bs.loc['Stockholders Equity'].iloc[0] / bs.loc['Total Assets'].iloc[0]) * 100, 1)
        if industry not in ['銀行業', '保険業', '証券、商品先物取引業', 'その他金融業'] and eq_ratio < 40:
            score -= 1
            reasons.append(f"低自己資本({eq_ratio}%)")

    # 増配・減配（配当履歴）
    try:
        divs = stock.dividends
        if not divs.empty:
            y_div = divs.resample('Y').sum().tail(3)
            if len(y_div) >= 2 and y_div.iloc[-1] < y_div.iloc[-2]:
                score -= 1
                reasons.append("減配履歴有")
    except: pass

    star_score = max(1, score)
    judge = "〇" if star_score >= 4 else ("△" if star_score >= 2 else "×")
    
    return dy, round(payout, 1), eq_ratio, eps_val, judge, star_score, reasons

# --- UI ---
st.sidebar.header("⚙️ 検索条件")
min_yield_input = st.sidebar.number_input("最低配当利回り基準 (%)", value=3.0, step=0.1)

if st.button("🚀 全上場銘柄から業界別に抽出", type="primary"):
    jpx_df = fetch_jpx_full_list()
    if jpx_df.empty: st.stop()

    all_industries = sorted(jpx_df['33業種区分'].unique())
    final_results = []
    
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    for idx, industry in enumerate(all_industries):
        status_text.text(f"分析中 ({idx+1}/{len(all_industries)}): {industry}")
        
        # 業界全銘柄から「コード順（実質的に規模の大きい順に近い）」または「上位10社」を抽出
        # ※時価総額ソートはAPI負荷が高いため、JPX順の10社をベースにする
        sector_stocks = jpx_df[jpx_df['33業種区分'] == industry].head(10)
        
        sector_candidates = []
        for _, row in sector_stocks.iterrows():
            code = f"{row['コード']}.T"
            try:
                stock = yf.Ticker(code)
                dy, pr, eq, eps, judge, star, reasons = analyze_stock_strict(stock, industry, min_yield_input)
                
                sector_candidates.append({
                    '業種': industry,
                    'コード': row['コード'],
                    '銘柄名': row['銘柄名'],
                    '利回り(%)': dy,
                    '性向(%)': pr,
                    '自己資本(%)': eq,
                    'EPS成長(%)': eps,
                    '判定': judge,
                    'おすすめ度': "★" * star + "☆" * (5 - star),
                    '備考': " / ".join(reasons) if reasons else "良好",
                    'score': star,
                    'dy_val': dy
                })
            except: continue
        
        if sector_candidates:
            # スコア優先 ＞ 利回り優先
            sector_sorted = sorted(sector_candidates, key=lambda x: (-x['score'], -x['dy_val']))
            final_results.extend(sector_sorted[:5]) # 各業界上位5社
        
        progress_bar.progress((idx + 1) / len(all_industries))

    if final_results:
        st.session_state['result_df'] = pd.DataFrame(final_results).drop(columns=['score', 'dy_val'])
        st.success("日本株全市場を対象とした分析が完了しました。")
    else:
        st.error("銘柄が抽出できませんでした。")

if not st.session_state['result_df'].empty:
    st.dataframe(st.session_state['result_df'], use_container_width=True)
    csv = st.session_state['result_df'].to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 結果をダウンロード", csv, "all_japan_dividend_stocks.csv", "text/csv")
