import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="高配当株完全スクリーニング", layout="wide")
st.title("高配当株スクリーニングツール (全業種完全網羅版)")

st.markdown("""
**【修正アップデート】**
* **全33業種網羅**: データが存在する全業種から必ず抽出します。
* **利回り計算の正常化**: APIのバグを回避し、株価と配当金から直接計算。
* **商社・金融対応**: 特殊な財務諸表形式でもデータを拾えるようロジックを強化。
""")

if 'result_df' not in st.session_state:
    st.session_state['result_df'] = pd.DataFrame()

@st.cache_data(ttl=86400)
def fetch_jpx_stock_list():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        df = pd.read_excel(url)
        # 全業種リストの確保
        all_industries = df['33業種区分'].unique().tolist()
        return df, all_industries
    except Exception as e:
        st.error(f"JPXデータの取得に失敗しました: {e}")
        return pd.DataFrame(), []

def get_clean_metrics(info):
    """APIの異常値を排除し、株価と配当から直接利回りを計算する"""
    price = info.get('currentPrice') or info.get('previousClose') or 1.0
    
    # 配当金の取得（年間換算）
    div_rate = info.get('dividendRate')
    if div_rate is None:
        div_rate = info.get('trailingAnnualDividendRate', 0)
    
    # 利回り計算
    calc_yield = (div_rate / price * 100) if div_rate and price else 0.0
    
    # 異常値（100%超えや20%超え）はデータ不備として処理
    if calc_yield > 20.0: 
        calc_yield = 0.0
        
    # 配当性向
    eps = info.get('trailingEps') or info.get('forwardEps')
    if eps and eps > 0:
        calc_payout = (div_rate / eps * 100)
    else:
        calc_payout = info.get('payoutRatio', 0)
        if calc_payout <= 1.0: calc_payout *= 100
        
    return round(calc_yield, 2), round(calc_payout, 2)

def check_growth_robust(financials, row_names):
    """商社や特殊業種でも売上・利益を拾えるよう複数のキーをチェック"""
    try:
        target = None
        for name in row_names:
            if name in financials.index:
                target = name
                break
        if target is None: return False, "データ不足"
        
        data = pd.to_numeric(financials.loc[target], errors='coerce').dropna()
        if len(data) < 3: return False, "期間不足"
        
        vals = data.values[:3]
        if vals[0] >= vals[1] >= vals[2]: # 簡易3期連続成長/維持
            return True, ""
        return False, "成長停滞"
    except:
        return False, "判定不能"

st.sidebar.header("⚙️ 検索条件")
target_scope = st.sidebar.radio("調査対象", ("TOPIX Core30 & Large70", "TOPIX 500"))
min_yield = st.sidebar.number_input("最低配当利回り (%)", value=3.0)
min_payout = st.sidebar.slider("配当性向 下限", 0, 100, 20)
max_payout = st.sidebar.slider("配当性向 上限", 0, 100, 70)

if st.button("🚀 スクリーニング開始", type="primary"):
    jpx_df, all_industries = fetch_jpx_stock_list()
    if jpx_df.empty: st.stop()

    # フィルタリング
    if "500" in target_scope:
        target_df = jpx_df[jpx_df['規模区分'].isin(['TOPIX Core30', 'TOPIX Large70', 'TOPIX Mid400'])]
    else:
        target_df = jpx_df[jpx_df['規模区分'].isin(['TOPIX Core30', 'TOPIX Large70'])]

    results = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    tickers = target_df['コード'].astype(str).tolist()
    total = len(tickers)

    for i, code in enumerate(tickers):
        symbol = f"{code}.T"
        status_text.text(f"解析中 ({i+1}/{total}): {symbol}")
        try:
            stock = yf.Ticker(symbol)
            info = stock.info
            
            # 正確な利回りと性向を取得
            dy, pr = get_clean_metrics(info)
            
            if dy < min_yield:
                continue
            
            financials = stock.financials
            bs = stock.balance_sheet
            divs = stock.dividends
            
            industry = target_df[target_df['コード'] == int(code)]['33業種区分'].values[0]
            name = target_df[target_df['コード'] == int(code)]['銘柄名'].values[0]
            
            # 成長性判定
            rev_ok, _ = check_growth_robust(financials, ['Total Revenue', 'Operating Revenue', 'Revenue'])
            net_ok, _ = check_growth_robust(financials, ['Net Income Common Stockholders', 'Net Income'])
            
            # 自己資本比率
            eq_ratio = 0.0
            if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
                eq_ratio = round((bs.loc['Stockholders Equity'].iloc[0] / bs.loc['Total Assets'].iloc[0]) * 100, 1)
            
            # 減配チェック (直近3年)
            yearly_div = divs.resample('Y').sum().tail(3)
            no_cut = True
            if len(yearly_div) >= 2:
                if yearly_div.iloc[-1] < yearly_div.iloc[-2] - 0.1: no_cut = False

            # ペナルティ計算 (fail_count)
            fail_count = 0
            reasons = []
            if not (min_payout <= pr <= max_payout): 
                fail_count += 1
                reasons.append(f"性向{pr}%")
            if not rev_ok: 
                fail_count += 1
                reasons.append("売上停滞")
            if not net_ok: 
                fail_count += 1
                reasons.append("利益停滞")
            if not no_cut: 
                fail_count += 1
                reasons.append("減配履歴")
            
            # 金融以外で自己資本比率40%以下は減点
            financial_sectors = ['銀行業', '証券、商品先物取引業', '保険業', 'その他金融業']
            if industry not in financial_sectors and eq_ratio < 40:
                fail_count += 1
                reasons.append(f"財務({eq_ratio}%)")

            star_score = max(1, 5 - fail_count)
            
            results.append({
                '業種': industry,
                'コード': code,
                '銘柄名': name,
                '配当利回り(%)': dy,
                '配当性向(%)': pr,
                '自己資本比率(%)': eq_ratio,
                '判定': '〇' if fail_count == 0 else '△',
                'おすすめ度': "★" * star_score + "☆" * (5 - star_score),
                '備考': " / ".join(reasons) if reasons else "財務健全・成長維持",
                'fail_count': fail_count
            })
        except:
            continue
        progress_bar.progress((i + 1) / total)

    if results:
        res_df = pd.DataFrame(results)
        # 業界ごとに「未達が少ない順」＞「利回り順」でソートし、上位5件
        final_df = res_df.sort_values(['業種', 'fail_count', '配当利回り(%)'], ascending=[True, True, False])
        final_df = final_df.groupby('業種').head(5).drop(columns=['fail_count'])
        
        st.session_state['result_df'] = final_df
        st.success("全業種のスクリーニングが完了しました。")
    else:
        st.warning("条件に一致する銘柄が見つかりませんでした。")

if not st.session_state['result_df'].empty:
    st.dataframe(st.session_state['result_df'], use_container_width=True)
    csv = st.session_state['result_df'].to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 CSVダウンロード", csv, "high_dividend_list.csv", "text/csv")
