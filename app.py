import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="プライム高配当株・最終安定版", layout="wide")
st.title("高配当株スクリーニング (抜本的エラー対策・完走版)")

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

def analyze_stock_final_bulletproof(symbol, industry):
    """
    関数呼び出しを最小限にし、すべての数値処理において
    Noneチェックをその場で行うインライン・ガード方式
    """
    try:
        stock = yf.Ticker(symbol)
        info = stock.info
        if not info or not isinstance(info, dict):
            return None
    except:
        return None

    # --- 数値取得と安全な計算（インライン処理） ---
    # 1. 株価
    p = info.get('currentPrice') or info.get('previousClose')
    price = float(p) if isinstance(p, (int, float)) and p > 0 else 1.0

    # 2. 配当金
    d = info.get('dividendRate') or info.get('trailingAnnualDividendRate')
    div_rate = float(d) if isinstance(d, (int, float)) else 0.0

    # 3. 利回り計算
    if div_rate <= 0:
        return None
    dy = round((div_rate / price * 100), 2)
    if dy < 3.0:
        return None

    # 4. 配当性向（直近 EPS）
    te = info.get('trailingEps')
    t_eps = float(te) if isinstance(te, (int, float)) else 0.0
    
    if t_eps <= 0:
        t_payout = 999.0  # 判定除外用
    else:
        t_payout = round((div_rate / t_eps * 100), 1)

    # 【絶対条件】性向70%超は除外
    if t_payout > 70.0:
        return None

    # 5. スコアリングと備考
    score = 5
    reasons = []
    if t_payout > 60.0:
        score -= 1
        reasons.append(f"性向高({t_payout}%)")
    
    # 来期予想（存在する場合のみ備考に追記）
    fe = info.get('forwardEps')
    f_eps = float(fe) if isinstance(fe, (int, float)) else 0.0
    if f_eps > 0:
        f_payout = round((div_rate / f_eps * 100), 1)
        if f_payout <= 70.0 and f_payout < t_payout:
            reasons.append(f"来期回復見込({f_payout}%)")

    # 6. 業界・自己資本比率
    sogo_shosha_codes = [8001, 8002, 8031, 8053, 8058, 2768, 8015]
    try:
        code_num = int(symbol.replace('.T',''))
        is_shosha = code_num in sogo_shosha_codes
    except:
        is_shosha = False

    current_ind = "総合商社" if is_shosha else industry
    is_special = any(x in current_ind for x in ['銀行', '保険', '証券', 'その他金融', '総合商社'])

    eq_ratio = 0.0
    try:
        bs = stock.balance_sheet
        if bs is not None and not bs.empty and 'Stockholders Equity' in bs.index:
            equity = bs.loc['Stockholders Equity'].iloc[0]
            assets = bs.loc['Total Assets'].iloc[0]
            if isinstance(equity, (int, float)) and isinstance(assets, (int, float)) and assets > 0:
                eq_ratio = round((equity / assets) * 100, 1)
                if not is_special and eq_ratio < 40 and eq_ratio > 0:
                    score -= 1; reasons.append(f"財務弱({eq_ratio}%)")
    except:
        pass

    star_score = max(1, score)
    # 時価総額の取得
    mc = info.get('marketCap')
    m_cap = float(mc) if isinstance(mc, (int, float)) else 0.0

    return {
        '業種': current_ind, 'コード': symbol.replace('.T',''), '銘柄名': info.get('shortName', '不明'),
        '利回り(%)': dy, '性向(%)': t_payout, '自己資本(%)': eq_ratio,
        '判定': "〇" if star_score >= 4 else "△", 
        'おすすめ度': "★" * star_score + "☆" * (5 - star_score),
        '備考': " / ".join(reasons) if reasons else "健全", 
        'score': star_score, 'm_cap': m_cap
    }

if st.button("🚀 抜本的修正版で実行", type="primary"):
    jpx_df = fetch_jpx_prime()
    if jpx_df.empty:
        st.error("JPXデータの取得に失敗しました。")
        st.stop()

    all_industries = sorted(jpx_df['33業種区分'].unique())
    final_results = []
    status_text = st.empty()
    progress_bar = st.progress(0)

    for idx, industry in enumerate(all_industries):
        status_text.text(f"分析中: {industry}")
        sector_members = jpx_df[jpx_df['33業種区分'] == industry]
        
        sector_candidates = []
        for _, row in sector_members.iterrows():
            res = analyze_stock_final_bulletproof(f"{row['コード']}.T", industry)
            if res:
                sector_candidates.append(res)
            time.sleep(0.05)
        
        if sector_candidates:
            # 業界内時価総額順
            sector_sorted = sorted(sector_candidates, key=lambda x: x['m_cap'], reverse=True)
            final_results.extend(sector_sorted[:5])
        
        progress_bar.progress((idx + 1) / len(all_industries))

    if final_results:
        st.session_state['result_df'] = pd.DataFrame(final_results).sort_values(['業種', 'score'], ascending=[True, False])
        st.success("スキャン完了！")

if not st.session_state['result_df'].empty:
    df_show = st.session_state['result_df'].reset_index(drop=True)
    
    def highlight_style(data):
        attr = 'background-color: #FFF2CC'
        # おすすめ度4以上かつ性向60%以下
        is_best = (data['おすすめ度'].str.count('★') >= 4) & (data['性向(%)'] <= 60.0)
        return [attr if v else '' for v in is_best]

    st.dataframe(df_show.style.apply(highlight_style, axis=1), use_container_width=True)
    st.download_button("📥 完全版CSV", df_show.to_csv(index=False).encode('utf-8-sig'), "prime_dividend_scan.csv")
