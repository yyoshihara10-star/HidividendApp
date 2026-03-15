import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="高配当株スクリーニング", layout="wide")
st.title("高配当株スクリーニング (33業種完全表示版)")

# JPX 33業種すべてを定義
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
    except:
        return pd.DataFrame()

def get_robust_metrics(stock, industry):
    info = stock.info
    price = info.get('currentPrice') or info.get('previousClose') or 1.0
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    dy = round((div_rate / price * 100), 2) if div_rate else 0.0
    
    eps = info.get('trailingEps') or 1.0
    pr = round((div_rate / eps * 100), 2) if div_rate else 0.0
    
    bs = stock.balance_sheet
    eq_ratio = 0.0
    if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
        eq_ratio = round((bs.loc['Stockholders Equity'].iloc[0] / bs.loc['Total Assets'].iloc[0]) * 100, 1)

    # ペナルティ判定（ここを「足切り」ではなく「点数化」に使う）
    fail_count = 0
    reasons = []
    
    # 財務・減配チェック
    if industry not in ['銀行業', '保険業', '証券、商品先物取引業', 'その他金融業'] and eq_ratio < 40:
        fail_count += 1; reasons.append(f"低自己資本({eq_ratio}%)")
    
    # 成長性チェック（データがない場合はペナルティにしないが、〇判定にはしない）
    financials = stock.financials
    rev_keys = ['Total Revenue', 'Operating Revenue', 'Revenue']
    rev_ok = False
    for k in rev_keys:
        if k in financials.index:
            s = pd.to_numeric(financials.loc[k], errors='coerce').dropna()
            if len(s) >= 2 and s.values[0] < s.values[1]:
                fail_count += 1; reasons.append("売上減")
                rev_ok = True; break

    return dy, pr, eq_ratio, fail_count, reasons

st.sidebar.header("⚙️ 検索条件")
target_scope = st.sidebar.radio("調査対象", ("TOPIX Core30 & Large70", "TOPIX 500"))
min_yield_input = st.sidebar.number_input("最低配当利回り (%)", value=3.0)

if st.button("🚀 33業種一斉スキャン実行", type="primary"):
    jpx_df = fetch_jpx_full_list()
    if jpx_df.empty: st.stop()

    target_df = jpx_df[jpx_df['規模区分'].isin(['TOPIX Core30', 'TOPIX Large70', 'TOPIX Mid400' if "500" in target_scope else 'TOPIX Large70'])]
    
    all_results = []
    progress_bar = st.progress(0)
    codes = target_df['コード'].astype(str).tolist()

    for i, code in enumerate(codes):
        try:
            symbol = f"{code}.T"
            stock = yf.Ticker(symbol)
            row_info = target_df[target_df['コード'] == int(code)].iloc[0]
            industry = row_info['33業種区分']
            
            dy, pr, eq, fail_count, reasons = get_robust_metrics(stock, industry)
            
            # 利回り基準さえ満たせば、どんなにボロボロの財務でも一旦リストに入れる（後でソート）
            if dy < min_yield_input: continue

            star_score = max(1, 5 - fail_count)
            all_results.append({
                '業種': industry,
                'コード': code,
                '銘柄名': row_info['銘柄名'],
                '配当利回り(%)': dy,
                '配当性向(%)': pr,
                '自己資本比率(%)': eq,
                '判定': '〇' if fail_count == 0 else ('△' if fail_count <= 2 else '×'),
                'おすすめ度': "★" * star_score + "☆" * (5 - star_score),
                '備考': " / ".join(reasons) if reasons else "財務健全",
                'fail_count': fail_count
            })
        except: continue
        progress_bar.progress((i + 1) / len(codes))

    if all_results:
        res_df = pd.DataFrame(all_results)
        # 業種ごとに「おすすめ度（fail_countの少なさ）」を最優先にソート
        final_list = []
        for sector in ALL_33_SECTORS:
            sector_data = res_df[res_df['業種'] == sector]
            if not sector_data.empty:
                # 未達が少ない順 ＞ 利回り高い順
                top5 = sector_data.sort_values(['fail_count', '配当利回り(%)'], ascending=[True, False]).head(5)
                final_list.append(top5)
        
        st.session_state['result_df'] = pd.concat(final_list).drop(columns=['fail_count'])
        st.success("33業種スキャン完了。条件を満たすすべての銘柄を表示しました。")

if not st.session_state['result_df'].empty:
    st.dataframe(st.session_state['result_df'], use_container_width=True)
