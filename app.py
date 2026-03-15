import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="プライム高配当株・安定版", layout="wide")
st.title("高配当株スクリーニング (初期安定版ベース)")

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
    """初期の正常動作版に、安全な備考追記のみを追加"""
    try:
        stock = yf.Ticker(symbol)
        info = stock.info
        if not info: return None
    except:
        return None

    # 利回り計算
    price = info.get('currentPrice') or info.get('previousClose') or 1.0
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    dy = round((div_rate / price * 100), 2)
    
    if dy < 3.0: return None

    # 配当性向（直近）
    t_eps = info.get('trailingEps', 0)
    t_payout = round((div_rate / t_eps * 100), 1) if t_eps and t_eps > 0 else 999

    # 性向70%超は除外
    if t_payout > 70.0: return None

    # おすすめ度判定
    score = 5
    reasons = []
    if t_payout > 60.0:
        score -= 1
        reasons.append(f"性向高({t_payout}%)")

    # --- 備考への注記（計算エラーを避けるため完全に独立して処理） ---
    f_eps = info.get('forwardEps')
    if isinstance(f_eps, (int, float)) and f_eps > 0:
        f_payout = round((div_rate / f_eps * 100), 1)
        if f_payout <= 70.0 and f_payout < t_payout:
            reasons.append(f"来期回復見込({f_payout}%)")

    # 業界判定
    sogo_shosha_codes = [8001, 8002, 8031, 8053, 8058, 2768, 8015]
    try:
        is_shosha = int(symbol.replace('.T','')) in sogo_shosha_codes
    except:
        is_shosha = False
    current_ind = "総合商社" if is_shosha else industry
    is_special = any(x in current_ind for x in ['銀行', '保険', '証券', 'その他金融', '総合商社'])

    # 自己資本比率
    eq_ratio = 0.0
    try:
        bs = stock.balance_sheet
        if bs is not None and not bs.empty and 'Stockholders Equity' in bs.index:
            eq = bs.loc['Stockholders Equity'].iloc[0]
            ta = bs.loc['Total Assets'].iloc[0]
            if eq and ta:
                eq_ratio = round((eq / ta) * 100, 1)
                if not is_special and eq_ratio < 40:
                    score -= 1; reasons.append(f"財務弱({eq_ratio}%)")
    except:
        pass

    star_score = max(1, score)
    return {
        '業種': current_ind, 'コード': symbol.replace('.T',''), '銘柄名': info.get('shortName', '不明'),
        '利回り(%)': dy, '性向(%)': t_payout, '自己資本(%)': eq_ratio,
        '判定': "〇" if star_score >= 4 else "△", 
        'おすすめ度': "★" * star_score + "☆" * (5 - star_score),
        '備考': " / ".join(reasons) if reasons else "健全", 
        'score': star_score, 'm_cap': info.get('marketCap', 0)
    }

if st.button("🚀 安定版で実行（完全差し戻し）", type="primary"):
    jpx_df = fetch_jpx_prime()
    if jpx_df.empty: st.stop()

    all_industries = sorted(jpx_df['33業種区分'].unique())
    final_results = []
    status_text = st.empty()
    progress_bar = st.progress(0)

    for idx, industry in enumerate(all_industries):
        status_text.text(f"分析中: {industry}")
        sector_members = jpx_df[jpx_df['33業種区分'] == industry]
        
        sector_candidates = []
        for _, row in sector_members.iterrows():
            res = analyze_stock(f"{row['コード']}.T", industry)
            if res: sector_candidates.append(res)
            time.sleep(0.05)
        
        if sector_candidates:
            # 時価総額順で上位5銘柄
            sector_sorted = sorted(sector_candidates, key=lambda x: x['m_cap'], reverse=True)
            final_results.extend(sector_sorted[:5])
        
        progress_bar.progress((idx + 1) / len(all_industries))

    if final_results:
        st.session_state['result_df'] = pd.DataFrame(final_results).sort_values(['業種', 'score'], ascending=[True, False])
        st.success("スキャン完了！")

if not st.session_state['result_df'].empty:
    df_show = st.session_state['result_df'].reset_index(drop=True)
    def highlight_best(data):
        attr = 'background-color: #FFF2CC'
        is_best = (data['おすすめ度'].str.count('★') >= 4) & (data['性向(%)'] <= 60.0)
        return [attr if v else '' for v in is_best]

    st.dataframe(df_show.style.apply(highlight_best, axis=1), use_container_width=True)
    st.download_button("📥 CSVダウンロード", df_show.to_csv(index=False).encode('utf-8-sig'), "stable_dividend_list.csv")
