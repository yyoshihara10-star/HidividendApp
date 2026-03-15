import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="プライム高配当株・完全網羅版", layout="wide")
st.title("高配当株スクリーニング (全業種網羅・商社独立・性向70%除外)")

if 'result_df' not in st.session_state:
    st.session_state['result_df'] = pd.DataFrame()

@st.cache_data(ttl=86400)
def fetch_jpx_prime():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        df = pd.read_excel(url)
        return df[df['市場・商品区分'].str.contains('プライム')]
    except:
        return pd.DataFrame()

def analyze_stock(symbol, industry):
    try:
        stock = yf.Ticker(symbol)
        info = stock.info
        if not info or not isinstance(info, dict): return None
    except:
        return None

    # 1. 株価と配当
    price = info.get('currentPrice') or info.get('previousClose') or 1.0
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    dy = round((div_rate / price * 100), 2)
    if dy < 3.0: return None

    # 2. 配当性向（70.1%以上は即除外）
    eps = info.get('trailingEps') or info.get('forwardEps') or 0
    payout = (div_rate / eps * 100) if eps > 0 else 999
    if payout > 70.0: return None

    # 3. 商社独立判定
    shosha_codes = [8001, 8002, 8031, 8053, 8058, 2768, 8015]
    code_int = int(symbol.replace('.T',''))
    display_industry = "総合商社" if code_int in shosha_codes else industry

    # 4. スコアリング
    score = 5
    reasons = []
    if payout > 60.0:
        score -= 1
        reasons.append(f"性向高({round(payout)}%)")
    
    is_special = any(x in display_industry for x in ['銀行', '保険', '証券', 'その他金融', '総合商社'])
    eq_ratio = 0.0
    try:
        bs = stock.balance_sheet
        if bs is not None and not bs.empty and 'Stockholders Equity' in bs.index:
            eq = bs.loc['Stockholders Equity'].iloc[0]
            at = bs.loc['Total Assets'].iloc[0]
            if eq and at and at > 0:
                eq_ratio = round((eq / at) * 100, 1)
                if not is_special and eq_ratio < 40:
                    score -= 1; reasons.append(f"財務({eq_ratio}%)")
    except: pass

    star_score = max(1, score)
    return {
        '業種': display_industry, 'コード': code_int, '銘柄名': info.get('shortName', '不明'),
        '利回り(%)': dy, '性向(%)': round(payout, 1), '自己資本(%)': eq_ratio,
        '判定': "〇" if star_score >= 4 else "△",
        'おすすめ度': "★" * star_score + "☆" * (5 - star_score),
        '備考': " / ".join(reasons) if reasons else "良好",
        'score': star_score, 'm_cap': info.get('marketCap', 0)
    }

if st.button("🚀 全業種スキャン実行", type="primary"):
    jpx_df = fetch_jpx_prime()
    if jpx_df.empty: st.stop()

    results = []
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    codes = jpx_df.to_dict('records')
    for idx, row in enumerate(codes):
        status_text.text(f"分析中 ({idx+1}/{len(codes)}): {row['銘柄名']}")
        res = analyze_stock(f"{row['コード']}.T", row['33業種区分'])
        if res:
            results.append(res)
        if idx % 50 == 0:
            progress_bar.progress((idx + 1) / len(codes))
    
    if results:
        df_all = pd.DataFrame(results)
        # 業種ごとに時価総額上位5件を抽出
        st.session_state['result_df'] = df_all.groupby('業種', as_index=False).apply(
            lambda x: x.nlargest(5, 'm_cap')
        ).reset_index(drop=True)
        st.success("スキャン完了！")

if not st.session_state['result_df'].empty:
    df_show = st.session_state['result_df'].copy()
    
    # 色付け関数
    def highlight_excellent(s):
        # 条件：★4つ以上 かつ 性向60%以下
        is_excellent = (s['おすすめ度'].count('★') >= 4) and (s['性向(%)'] <= 60.0)
        return ['background-color: #FFF2CC' if is_excellent else '' for _ in s]

    cols = ['業種', 'コード', '銘柄名', '利回り(%)', '性向(%)', '自己資本(%)', '判定', 'おすすめ度', '備考']
    styled_df = df_show[cols].style.apply(highlight_excellent, axis=1)
    
    st.dataframe(styled_df, use_container_width=True)
    st.download_button("📥 完全網羅版CSVをダウンロード", df_show[cols].to_csv(index=False).encode('utf-8-sig'), "prime_dividend_all_sectors.csv")
