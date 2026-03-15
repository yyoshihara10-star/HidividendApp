import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="プライム高配当株・完全修正版", layout="wide")
st.title("高配当株スクリーニング (救済ロジック＆エラー完全ガード版)")

if 'result_df' not in st.session_state:
    st.session_state['result_df'] = pd.DataFrame()

@st.cache_data(ttl=86400)
def fetch_jpx_prime():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        df = pd.read_excel(url)
        return df[df['市場・商品区分'].str.contains('プライム')]
    except Exception as e:
        st.error(f"JPXデータの読み込みエラー: {e}")
        return pd.DataFrame()

def analyze_stock_final_retry(symbol, industry):
    stock = yf.Ticker(symbol)
    info = None
    # 最大3回リトライ
    for i in range(3):
        try:
            info = stock.info
            if info and 'shortName' in info: break
        except:
            time.sleep(2 * (i + 1))
    
    if not info or not isinstance(info, dict): return None

    # 1. 基本データ取得 (None対策)
    price = info.get('currentPrice') or info.get('previousClose') or 1.0
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    dy = round((div_rate / price * 100), 2)
    if dy < 3.0: return None

    # 2. 配当性向の判定 (救済ロジック含む)
    t_eps = info.get('trailingEps')
    f_eps = info.get('forwardEps')
    
    # 性向計算 (データがない場合は極端な値を入れて判定)
    t_payout = (div_rate / t_eps * 100) if (t_eps and t_eps > 0) else 999
    f_payout = (div_rate / f_eps * 100) if (f_eps and f_eps > 0) else 999
    
    is_recovery_safe = False
    display_payout = t_payout

    # 【重要】救済判定
    if t_payout > 70.0:
        if f_payout <= 70.0 and f_payout != 0:
            is_recovery_safe = True
            display_payout = f_payout
        else:
            return None # 回復見込みもなければ除外

    # 3. 財務・判定
    score = 5
    reasons = []
    if is_recovery_safe:
        score -= 1
        reasons.append(f"業績回復期待(来期性向{round(f_payout,1)}%)")
    elif t_payout > 60.0:
        score -= 1
        reasons.append(f"性向高({round(t_payout,1)}%)")

    # 業界・商社判定
    sogo_shosha_codes = [8001, 8002, 8031, 8053, 8058, 2768, 8015]
    is_shosha = int(symbol.replace('.T','')) in sogo_shosha_codes
    current_industry = "総合商社" if is_shosha else industry
    is_special = any(x in current_industry for x in ['銀行', '保険', '証券', 'その他金融', '総合商社'])

    eq_ratio = 0.0
    try:
        bs = stock.balance_sheet
        if bs is not None and not bs.empty and 'Stockholders Equity' in bs.index:
            eq_ratio = round((bs.loc['Stockholders Equity'].iloc[0] / bs.loc['Total Assets'].iloc[0]) * 100, 1)
            if not is_special and eq_ratio < 40:
                score -= 1; reasons.append(f"財務弱({eq_ratio}%)")
    except:
        pass

    star_score = max(1, score)
    return {
        '業種': current_industry, 'コード': symbol.replace('.T',''), '銘柄名': info.get('shortName', '不明'),
        '利回り(%)': dy, '性向(%)': round(display_payout, 1), '自己資本(%)': eq_ratio,
        '判定': "〇" if star_score >= 4 else "△", 
        'おすすめ度': "★" * star_score + "☆" * (5 - star_score),
        '備考': " / ".join(reasons) if reasons else "健全", 
        'score': star_score, 'm_cap': info.get('marketCap', 0)
    }

if st.button("🚀 完全修正版スキャン実行", type="primary"):
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
            res = analyze_stock_final_retry(f"{row['コード']}.T", industry)
            if res:
                sector_candidates.append(res)
            time.sleep(0.15) # 負荷軽減
        
        if sector_candidates:
            # 業界全銘柄から時価総額順でソート
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
        # ★4つ以上、かつ性向60%以下のクリーンな銘柄をハイライト
        is_best = (data['おすすめ度'].str.count('★') >= 4) & (data['性向(%)'] <= 60.0)
        return [attr if v else '' for v in is_best]

    styled_df = df_show.style.apply(highlight_best, axis=1)
    st.dataframe(styled_df, use_container_width=True)
    st.download_button("📥 最終版CSVダウンロード", df_show.to_csv(index=False).encode('utf-8-sig'), "prime_dividend_final_v2.csv")
