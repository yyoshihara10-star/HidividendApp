import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="プライム高配当株・高信頼版", layout="wide")
st.title("高配当株スクリーニング (リトライ機能搭載・完全捕捉版)")

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

def get_info_with_retry(symbol, max_retries=3):
    """エラー時に時間をおいて再試行する関数"""
    stock = yf.Ticker(symbol)
    for i in range(max_retries):
        try:
            info = stock.info
            if info and 'shortName' in info:
                return info, stock
        except Exception:
            if i < max_retries - 1:
                time.sleep(2 * (i + 1)) # 失敗するごとに待機時間を伸ばす
            continue
    return None, None

def analyze_stock_resilient(symbol, industry):
    info, stock = get_info_with_retry(symbol)
    if not info: return None

    # 基本指標の抽出
    price = info.get('currentPrice') or info.get('previousClose') or 1.0
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    dy = round((div_rate / price * 100), 2)
    
    # 利回り3%未満は足切り
    if dy < 3.0: return None

    # 性向判定
    t_eps = info.get('trailingEps')
    f_eps = info.get('forwardEps')
    t_payout = round((div_rate / t_eps * 100), 1) if t_eps and t_eps > 0 else 999
    f_payout = round((div_rate / f_eps * 100), 1) if f_eps and f_eps > 0 else 999
    
    is_recovery = False
    display_payout = t_payout
    if t_payout > 70.0:
        if f_payout <= 70.0:
            is_recovery = True
            display_payout = f_payout
        else:
            return None # 両方70%超は除外

    # 財務スコアリング
    score = 5
    reasons = []
    if is_recovery:
        score -= 1
        reasons.append(f"来期回復期待({f_payout}%)")
    elif t_payout > 60.0:
        score -= 1
        reasons.append(f"性向高({t_payout}%)")

    # 業界・商社判定
    sogo_shosha_codes = [8001, 8002, 8031, 8053, 8058, 2768, 8015]
    is_shosha = int(symbol.replace('.T','')) in sogo_shosha_codes
    current_industry = "総合商社" if is_shosha else industry
    is_special = any(x in current_industry for x in ['銀行', '保険', '証券', 'その他金融', '総合商社'])

    # 自己資本比率取得
    eq_ratio = 0.0
    try:
        bs = stock.balance_sheet
        if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
            eq_ratio = round((bs.loc['Stockholders Equity'].iloc[0] / bs.loc['Total Assets'].iloc[0]) * 100, 1)
            if not is_special and eq_ratio < 40:
                score -= 1; reasons.append(f"財務弱({eq_ratio}%)")
    except:
        pass

    star_score = max(1, score)
    return {
        '業種': current_industry, 'コード': symbol.replace('.T',''), '銘柄名': info.get('shortName'),
        '利回り(%)': dy, '性向(%)': display_payout, '自己資本(%)': eq_ratio,
        '判定': "〇" if star_score >= 4 else "△", 
        'おすすめ度': "★" * star_score + "☆" * (5 - star_score),
        '備考': " / ".join(reasons) if reasons else "健全", 
        'score': star_score, 'm_cap': info.get('marketCap', 0)
    }

if st.button("🚀 徹底再試行スキャン開始", type="primary"):
    jpx_df = fetch_jpx_prime()
    if jpx_df.empty: st.stop()

    all_industries = sorted(jpx_df['33業種区分'].unique())
    final_results = []
    status_text = st.empty()
    progress_bar = st.progress(0)

    for idx, industry in enumerate(all_industries):
        status_text.text(f"粘り強く分析中: {industry}")
        sector_members = jpx_df[jpx_df['33業種区分'] == industry]
        
        sector_candidates = []
        for _, row in sector_members.iterrows():
            res = analyze_stock_resilient(f"{row['コード']}.T", industry)
            if res:
                sector_candidates.append(res)
            time.sleep(0.1) # 通信間隔の安定
        
        if sector_candidates:
            sector_sorted = sorted(sector_candidates, key=lambda x: x['m_cap'], reverse=True)
            final_results.extend(sector_sorted[:5])
        
        progress_bar.progress((idx + 1) / len(all_industries))

    if final_results:
        st.session_state['result_df'] = pd.DataFrame(final_results).sort_values(['業種', 'score'], ascending=[True, False])
        st.success("スキャン完了！")

if not st.session_state['result_df'].empty:
    df_show = st.session_state['result_df'].reset_index(drop=True)
    
    def highlight_logic(data):
        attr = 'background-color: #FFF2CC'
        # 厳選条件：★4以上かつ性向60%以下
        is_best = (data['おすすめ度'].str.count('★') >= 4) & (data['性向(%)'] <= 60.0)
        return [attr if v else '' for v in is_best]

    styled_df = df_show.style.apply(highlight_logic, axis=1)
    st.dataframe(styled_df, use_container_width=True)
    st.download_button("📥 完全データ版CSV", df_show.to_csv(index=False).encode('utf-8-sig'), "prime_resilient_dividend.csv")
