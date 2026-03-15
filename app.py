import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="プライム高配当株・安定版", layout="wide")
st.title("高配当株スクリーニング (安定ロジック回帰版)")

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

def analyze_stock_fixed(symbol, industry):
    """計算エラーを完全に排除した安定版ロジック"""
    stock = yf.Ticker(symbol)
    info = None
    for i in range(2):
        try:
            info = stock.info
            if info and isinstance(info, dict) and 'shortName' in info: break
        except:
            time.sleep(1)
    
    if not info: return None

    # --- 数値取得の安全関数 ---
    def get_num(key):
        val = info.get(key)
        return val if isinstance(val, (int, float)) else 0

    price = get_num('currentPrice') or get_num('previousClose') or 1.0
    div_rate = get_num('dividendRate') or get_num('trailingAnnualDividendRate')
    
    # 1. 利回り判定
    dy = round((div_rate / price * 100), 2) if div_rate > 0 else 0
    if dy < 3.0: return None

    # 2. 配当性向判定 (直近のみで判定)
    t_eps = get_num('trailingEps')
    f_eps = get_num('forwardEps')
    
    t_payout = round((div_rate / t_eps * 100), 1) if t_eps > 0 else 999
    f_payout = round((div_rate / f_eps * 100), 1) if f_eps > 0 else 0

    # 【重要】直近性向70.1%以上は例外なく除外（前の安定状態を維持）
    if t_payout > 70.0: return None

    score = 5
    reasons = []
    
    # 指標評価
    if t_payout > 60.0:
        score -= 1
        reasons.append(f"性向高({t_payout}%)")
    
    # 業績回復見込みがある場合は備考に追記（判定には影響させない）
    if 0 < f_payout <= 70.0 and f_payout < t_payout:
        reasons.append(f"来期回復見込({f_payout}%)")

    # 業界・商社判定
    sogo_shosha_codes = [8001, 8002, 8031, 8053, 8058, 2768, 8015]
    is_shosha = int(symbol.replace('.T','')) in sogo_shosha_codes
    current_ind = "総合商社" if is_shosha else industry
    is_special = any(x in current_ind for x in ['銀行', '保険', '証券', 'その他金融', '総合商社'])

    # 自己資本比率
    eq_ratio = 0.0
    try:
        bs = stock.balance_sheet
        if bs is not None and not bs.empty and 'Stockholders Equity' in bs.index:
            equity = bs.loc['Stockholders Equity'].iloc[0]
            assets = bs.loc['Total Assets'].iloc[0]
            if equity and assets and assets != 0:
                eq_ratio = round((equity / assets) * 100, 1)
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

if st.button("🚀 安定版で実行（差し戻し適用済）", type="primary"):
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
            res = analyze_stock_fixed(f"{row['コード']}.T", industry)
            if res: sector_candidates.append(res)
            time.sleep(0.05)
        
        if sector_candidates:
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
        # ★4以上かつ性向60%以下の優良株をハイライト
        is_best = (data['おすすめ度'].str.count('★') >= 4) & (data['性向(%)'] <= 60.0)
        return [attr if v else '' for v in is_best]

    styled_df = df_show.style.apply(highlight_best, axis=1)
    st.dataframe(styled_df, use_container_width=True)
    st.download_button("📥 CSVダウンロード", df_show.to_csv(index=False).encode('utf-8-sig'), "prime_stable_high_dividend.csv")
