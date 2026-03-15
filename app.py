import streamlit as st
import pandas as pd
import yfinance as yf
import time

# --- 初期設定 ---
st.set_page_config(page_title="高配当株スクリーニング", layout="wide")
st.title("高配当株スクリーニング (33業種完全網羅・エラー修正版)")

# セッション状態の初期化
if 'result_df' not in st.session_state:
    st.session_state['result_df'] = pd.DataFrame()

# --- JPX全33業種の定義 ---
ALL_33_SECTORS = [
    "水産・農林業", "鉱業", "建設業", "食料品", "繊維製品", "パルプ・紙", "化学", "医薬品", 
    "石油・石炭製品", "ゴム製品", "ガラス・土石製品", "鉄鋼", "非鉄金属", "金属製品", 
    "機械", "電気機器", "輸送用機器", "精密機器", "その他製品", "電気・ガス業", 
    "陸運業", "海運業", "空運業", "倉庫・運輸関連業", "情報・通信業", "卸売業", 
    "小売業", "銀行業", "証券、商品先物取引業", "保険業", "その他金融業", "不動産業", "サービス業"
]

@st.cache_data(ttl=86400)
def fetch_jpx_full_list():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        return pd.read_excel(url)
    except Exception as e:
        st.error(f"JPXデータの取得に失敗しました: {e}")
        return pd.DataFrame()

def analyze_stock(stock, industry):
    """銘柄の財務データを厳格に分析し、判定を下す"""
    info = stock.info
    price = info.get('currentPrice') or info.get('previousClose') or 1.0
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    dy = round((div_rate / price * 100), 2) if div_rate else 0.0
    
    eps = info.get('trailingEps') or 1.0
    pr = round((div_rate / eps * 100), 2) if div_rate else 0.0
    
    # 財務・成長性チェック
    fail_count = 0
    reasons = []
    
    # 1. 自己資本比率
    bs = stock.balance_sheet
    eq_ratio = 0.0
    if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
        try:
            eq_ratio = round((bs.loc['Stockholders Equity'].iloc[0] / bs.loc['Total Assets'].iloc[0]) * 100, 1)
            if industry not in ['銀行業', '保険業', '証券、商品先物取引業', 'その他金融業'] and eq_ratio < 40:
                fail_count += 1
                reasons.append(f"低自己資本({eq_ratio}%)")
        except: pass

    # 2. 売上成長 (商社対応)
    financials = stock.financials
    rev_ok = False
    for k in ['Total Revenue', 'Operating Revenue', 'Revenue']:
        if k in financials.index:
            s = pd.to_numeric(financials.loc[k], errors='coerce').dropna()
            if len(s) >= 2 and s.values[0] < s.values[1]: # 直近が前期を下回る
                fail_count += 1
                reasons.append("売上減")
            rev_ok = True
            break
    if not rev_ok: reasons.append("売上データ無")

    # 判定
    if fail_count == 0:
        judge = '〇'
    elif fail_count <= 2:
        judge = '△'
    else:
        judge = '×'
        
    star_score = max(1, 5 - fail_count)
    return dy, pr, eq_ratio, judge, star_score, reasons, fail_count

# --- UI ---
st.sidebar.header("⚙️ 検索条件")
target_scope = st.sidebar.radio("調査対象", ("TOPIX Core30 & Large70", "TOPIX 500"))
min_yield_input = st.sidebar.number_input("最低配当利回り (%)", value=3.0, step=0.1)

if st.button("🚀 分析開始 (33業種すべて表示)", type="primary"):
    jpx_df = fetch_jpx_full_list()
    if jpx_df.empty: st.stop()

    # 規模区分によるフィルタ
    if "500" in target_scope:
        target_df = jpx_df[jpx_df['規模区分'].isin(['TOPIX Core30', 'TOPIX Large70', 'TOPIX Mid400'])]
    else:
        target_df = jpx_df[jpx_df['規模区分'].isin(['TOPIX Core30', 'TOPIX Large70'])]
    
    all_results = []
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    tickers = target_df['コード'].astype(str).tolist()
    
    for i, code in enumerate(tickers):
        symbol = f"{code}.T"
        status_text.text(f"解析中: {symbol} ({i+1}/{len(tickers)})")
        try:
            stock = yf.Ticker(symbol)
            row_info = target_df[target_df['コード'] == int(code)].iloc[0]
            industry = row_info['33業種区分']
            
            dy, pr, eq, judge, star, reasons, fail_c = analyze_stock(stock, industry)
            
            # 利回り基準さえ満たせば、どんな判定でもリストに入れる
            if dy >= min_yield_input:
                all_results.append({
                    '業種': industry,
                    'コード': code,
                    '銘柄名': row_info['銘柄名'],
                    '配当利回り(%)': dy,
                    '配当性向(%)': pr,
                    '自己資本比率(%)': eq,
                    '判定': judge,
                    'おすすめ度': "★" * star + "☆" * (5 - star),
                    '備考': " / ".join(reasons) if reasons else "財務健全",
                    'fail_count': fail_c
                })
        except: continue
        progress_bar.progress((i + 1) / len(tickers))

    if all_results:
        res_df = pd.DataFrame(all_results)
        final_list = []
        # 各業種ごとに、おすすめ順(fail_countが少ない順)に最大5件抽出
        for sector in ALL_33_SECTORS:
            sector_data = res_df[res_df['業種'] == sector]
            if not sector_data.empty:
                top5 = sector_data.sort_values(['fail_count', '配当利回り(%)'], ascending=[True, False]).head(5)
                final_list.append(top5)
        
        st.session_state['result_df'] = pd.concat(final_list).drop(columns=['fail_count'])
        st.success("スキャン完了！全業種のデータを抽出しました。")
    else:
        st.warning("条件に合う銘柄がありませんでした。利回りの設定を下げてみてください。")

# 結果表示
if not st.session_state['result_df'].empty:
    st.dataframe(st.session_state['result_df'], use_container_width=True)
    csv = st.session_state['result_df'].to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 CSVで保存", csv, "dividend_stocks.csv", "text/csv")
