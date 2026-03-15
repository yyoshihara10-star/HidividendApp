import streamlit as st
import pandas as pd
import yfinance as yf

st.set_page_config(page_title="プライム高配当株・厳選ロジック版", layout="wide")
st.title("高配当株スクリーニング (性向70%超除外版)")

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

def analyze_stock_strict_v2(symbol, industry):
    stock = yf.Ticker(symbol)
    info = stock.info
    
    # --- 利回り取得 ---
    price = info.get('currentPrice') or info.get('previousClose') or 1.0
    dy_val = info.get('trailingAnnualDividendYield') or info.get('yield')
    if dy_val and dy_val > 0:
        dy = round(dy_val * 100, 2)
    else:
        div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
        dy = round((div_rate / price * 100), 2)
    
    if dy < 3.0: return None

    # --- 配当性向の再計算 ---
    eps = info.get('trailingEps') or info.get('forwardEps')
    actual_div = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    
    if eps and eps > 0 and actual_div > 0:
        payout = (actual_div / eps) * 100
    else:
        payout = (info.get('payoutRatio', 0)) * 100 if info.get('payoutRatio', 0) <= 1.0 else info.get('payoutRatio', 0)

    # 【今回追加の改修】配当性向70%超を完全に除外
    if payout > 70.0: return None

    score = 5
    reasons = []
    
    # --- 指標チェック ---
    # 60%以上はおすすめ度を下げる
    if payout > 60:
        score -= 1
        reasons.append(f"性向高({round(payout)}%)")
    elif payout < 20:
        reasons.append(f"低性向({round(payout)}%)")

    # 売上・利益の連続成長
    financials = stock.financials
    rev_keys = ['Total Revenue', 'Operating Revenue', 'Revenue']
    growth_found = False
    for k in rev_keys:
        if k in financials.index:
            vals = pd.to_numeric(financials.loc[k], errors='coerce').dropna().values[:3]
            if len(vals) >= 2 and vals[0] < vals[1]:
                score -= 1; reasons.append("売上減")
            growth_found = True; break
    if not growth_found: reasons.append("データ不足")

    # 自己資本比率
    bs = stock.balance_sheet
    sogo_shosha_codes = [8001, 8002, 8031, 8053, 8058, 2768, 8015]
    is_shosha = int(symbol.replace('.T','')) in sogo_shosha_codes
    current_industry = "総合商社" if is_shosha else industry
    
    is_special = any(x in current_industry for x in ['銀行', '保険', '証券', 'その他金融', '総合商社'])
    
    if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
        try:
            eq_ratio = round((bs.loc['Stockholders Equity'].iloc[0] / bs.loc['Total Assets'].iloc[0]) * 100, 1)
            if not is_special and eq_ratio < 40:
                score -= 1; reasons.append(f"財務({eq_ratio}%)")
        except: pass
    else:
        eq_ratio = 0.0

    star_score = max(1, score)
    judge = "〇" if star_score >= 4 else ("△" if star_score >= 2 else "×")
    
    return {
        '業種': current_industry, '利回り(%)': dy, '性向(%)': round(payout, 1), 
        '自己資本(%)': eq_ratio, '判定': judge, 'おすすめ度': "★" * star_score + "☆" * (5 - star_score),
        '備考': " / ".join(reasons) if reasons else "良好(低性向・健全)",
        'score': star_score, 'm_cap': info.get('marketCap', 0)
    }

if st.button("🚀 厳格ロジックでスキャン開始", type="primary"):
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
            code = f"{row['コード']}.T"
            try:
                res = analyze_stock_strict_v2(code, industry)
                if res:
                    res.update({'コード': row['コード'], '銘柄名': row['銘柄名']})
                    sector_candidates.append(res)
            except: continue
        
        if sector_candidates:
            sector_sorted = sorted(sector_candidates, key=lambda x: x['m_cap'], reverse=True)
            final_results.extend(sector_sorted[:5])
        
        progress_bar.progress((idx + 1) / len(all_industries))

    if final_results:
        cols = ['業種', 'コード', '銘柄名', '利回り(%)', '性向(%)', '自己資本(%)', '判定', 'おすすめ度', '備考']
        df = pd.DataFrame(final_results)[cols]
        st.session_state['result_df'] = df.sort_values(['業種', 'おすすめ度'], ascending=[True, False])
        st.success("スキャン完了！")

def highlight_rows(df):
    style_df = pd.DataFrame('', index=df.index, columns=df.columns)
    for industry in df['業種'].unique():
        subset = df[df['業種'] == industry]
        max_stars = subset['おすすめ度'].max()
        if max_stars.count('★') >= 4:
            target_indices = subset[subset['おすすめ度'] == max_stars].index
            for idx in target_indices:
                style_df.loc[idx, :] = 'background-color: #FFF2CC'
    return style_df

if not st.session_state['result_df'].empty:
    df_to_show = st.session_state['result_df'].reset_index(drop=True)
    styled_df = df_to_show.style.apply(highlight_rows, axis=None)
    st.dataframe(styled_df, use_container_width=True)
    
    csv = df_to_show.to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 修正版CSVダウンロード", csv, "prime_strict_payout.csv", "text/csv")
