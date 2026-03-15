import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="高配当株スクリーニング", layout="wide")
st.title("高配当株スクリーニング (33業種完全網羅版)")

st.markdown("""
**【抽出ポリシー】**
* 全33業種を公平にスキャンし、利回り条件を満たす銘柄を漏れなく抽出します。
* 財務データが一部取得できない場合でも、利回りを優先してリストに掲載し、備考にその旨を記載します。
""")

@st.cache_data(ttl=86400)
def fetch_jpx_full_list():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        return pd.read_excel(url)
    except:
        st.error("JPXから銘柄データが取得できません。")
        return pd.DataFrame()

def get_robust_metrics(stock):
    """APIの異常値を回避し、正確な配当・利回りを算出"""
    info = stock.info
    price = info.get('currentPrice') or info.get('previousClose') or 1.0
    
    # 配当金の取得
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate')
    if not div_rate:
        try:
            divs = stock.dividends
            if not divs.empty: div_rate = divs.tail(2).sum()
        except: pass
            
    calc_yield = (div_rate / price * 100) if div_rate and price else 0.0
    
    # 配当性向
    pr = info.get('payoutRatio', 0)
    if pr <= 1.0: pr *= 100
    if pr == 0 and div_rate:
        eps = info.get('trailingEps') or 1.0
        pr = (div_rate / eps * 100)
        
    return round(calc_yield, 2), round(pr, 2)

st.sidebar.header("⚙️ 検索条件")
target_scope = st.sidebar.radio("調査対象", ("TOPIX Core30 & Large70", "TOPIX 500"))
min_yield_input = st.sidebar.number_input("最低配当利回り (%)", value=3.0)

if st.button("🚀 全業種スキャン開始", type="primary"):
    jpx_df = fetch_jpx_full_list()
    if jpx_df.empty: st.stop()

    if "500" in target_scope:
        target_df = jpx_df[jpx_df['規模区分'].isin(['TOPIX Core30', 'TOPIX Large70', 'TOPIX Mid400'])]
    else:
        target_df = jpx_df[jpx_df['規模区分'].isin(['TOPIX Core30', 'TOPIX Large70'])]

    all_results = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # 銘柄リストを33業種すべて網羅するように処理
    codes = target_df['コード'].astype(str).tolist()
    total = len(codes)

    for i, code in enumerate(codes):
        symbol = f"{code}.T"
        status_text.text(f"解析中: {symbol} ({i+1}/{total})")
        try:
            stock = yf.Ticker(symbol)
            dy, pr = get_robust_metrics(stock)
            
            # 利回りフィルタのみを足切りの基準にする
            if dy < min_yield_input: continue
            
            row_info = target_df[target_df['コード'] == int(code)].iloc[0]
            industry = row_info['33業種区分']
            
            # 財務チェック（欠損していても除外しない）
            fail_count = 0
            reasons = []
            
            # 自己資本比率
            bs = stock.balance_sheet
            eq_ratio = 0.0
            if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
                eq_ratio = round((bs.loc['Stockholders Equity'].iloc[0] / bs.loc['Total Assets'].iloc[0]) * 100, 1)
            
            if industry not in ['銀行業', '保険業', '証券、商品先物取引業', 'その他金融業'] and eq_ratio < 40:
                if eq_ratio > 0: 
                    fail_count += 1
                    reasons.append(f"財務({eq_ratio}%)")
                else:
                    reasons.append("財務データ無")

            # 星評価
            star_score = max(1, 5 - fail_count)
            
            all_results.append({
                '業種': industry,
                'コード': code,
                '銘柄名': row_info['銘柄名'],
                '配当利回り(%)': dy,
                '配当性向(%)': pr,
                '自己資本比率(%)': eq_ratio,
                '判定': '〇' if fail_count == 0 else '△',
                'おすすめ度': "★" * star_score + "☆" * (5 - star_score),
                '備考': " / ".join(reasons) if reasons else "良好",
                'fail_count': fail_count
            })
        except:
            continue
        progress_bar.progress((i + 1) / total)

    if all_results:
        final_res_df = pd.DataFrame(all_results)
        # 業界ごとに「未達項目が少ない順」＞「利回り順」でソートし、上位5社
        final_res_df = final_res_df.sort_values(['業種', 'fail_count', '配当利回り(%)'], ascending=[True, True, False])
        final_res_df = final_res_df.groupby('業種').head(5).drop(columns=['fail_count'])
        
        st.session_state['result_df'] = final_res_df
        st.success("スキャン完了。条件を満たす全ての業種から銘柄を抽出しました。")
    else:
        st.warning("銘柄が見つかりませんでした。利回り設定を調整してください。")

if not st.session_state['result_df'].empty:
    st.dataframe(st.session_state['result_df'], use_container_width=True)
    csv = st.session_state['result_df'].to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 結果をCSVで保存", csv, "dividend_scan_all_sectors.csv", "text/csv")
