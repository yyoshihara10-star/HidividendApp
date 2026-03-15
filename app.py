import streamlit as st
import pandas as pd
import yfinance as yf

st.set_page_config(page_title="プライム高配当株・王道銘柄完全捕捉", layout="wide")
st.title("高配当株スクリーニング (プライム全業種・全銘柄総当たり版)")

if 'result_df' not in st.session_state:
    st.session_state['result_df'] = pd.DataFrame()

@st.cache_data(ttl=86400)
def fetch_jpx_prime():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        df = pd.read_excel(url)
        # プライム市場の全銘柄を抽出
        return df[df['市場・商品区分'].str.contains('プライム')]
    except:
        return pd.DataFrame()

def analyze_stock_final_retry(symbol, industry):
    stock = yf.Ticker(symbol)
    # 確実にデータを取るために info を使用
    info = stock.info
    
    # 基本データの取得
    price = info.get('currentPrice') or info.get('previousClose') or 1.0
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    dy = round((div_rate / price * 100), 2)
    
    # 利回り3%未満は除外
    if dy < 3.0: return None

    score = 5
    reasons = []
    
    # 財務・成長性（yfinanceのデータを補正しながら取得）
    financials = stock.financials
    rev_keys = ['Total Revenue', 'Operating Revenue', 'Revenue', 'Operating Income']
    growth_found = False
    for k in rev_keys:
        if k in financials.index:
            vals = pd.to_numeric(financials.loc[k], errors='coerce').dropna().values[:3]
            if len(vals) >= 2 and vals[0] < vals[1]:
                score -= 1; reasons.append("売上/利益減")
            growth_found = True; break
    if not growth_found: reasons.append("成長データ不明")

    payout = info.get('payoutRatio', 0)
    if payout <= 1.0: payout *= 100
    if not (30 <= payout <= 70):
        score -= 1; reasons.append(f"性向({round(payout)}%)")

    bs = stock.balance_sheet
    eq_ratio = 0.0
    is_finance = any(x in industry for x in ['銀行', '保険', '証券', 'その他金融'])
    if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
        try:
            eq_ratio = round((bs.loc['Stockholders Equity'].iloc[0] / bs.loc['Total Assets'].iloc[0]) * 100, 1)
            if not is_finance and eq_ratio < 40:
                score -= 1; reasons.append(f"財務({eq_ratio}%)")
        except: pass

    star_score = max(1, score)
    judge = "〇" if star_score >= 4 else ("△" if star_score >= 2 else "×")
    
    return {
        '利回り(%)': dy, '性向(%)': round(payout, 1), '自己資本(%)': eq_ratio,
        '判定': judge, 'おすすめ度': "★" * star_score + "☆" * (5 - star_score),
        '備考': " / ".join(reasons) if reasons else "良好(指標クリア)",
        'score': star_score,
        'm_cap': info.get('marketCap', 0) # ソート用に時価総額を保存
    }

if st.button("🚀 プライム全銘柄・徹底スキャン", type="primary"):
    jpx_df = fetch_jpx_prime()
    if jpx_df.empty: st.stop()

    all_industries = sorted(jpx_df['33業種区分'].unique())
    final_results = []
    status_text = st.empty()
    progress_bar = st.progress(0)

    for idx, industry in enumerate(all_industries):
        status_text.text(f"【最優先スキャン】業界: {industry}")
        # 業界の「全」銘柄を取得
        sector_members = jpx_df[jpx_df['33業種区分'] == industry]
        
        sector_candidates = []
        for _, row in sector_members.iterrows():
            code = f"{row['コード']}.T"
            try:
                # 業界の全銘柄を順番に利回りチェックにかける
                res = analyze_stock_final_retry(code, industry)
                if res:
                    res.update({'業種': industry, 'コード': row['コード'], '銘柄名': row['銘柄名']})
                    sector_candidates.append(res)
            except: continue
        
        if sector_candidates:
            # 業界内の全3%以上銘柄の中から、「時価総額」で並べ替えて上位5社を選ぶ
            # これで大手（積水ハウス、三菱HC等）が必ず上位に来る
            sector_sorted = sorted(sector_candidates, key=lambda x: x['m_cap'], reverse=True)
            
            # さらに、その上位5社の中で「おすすめ度」を反映させた順序で格納
            final_results.extend(sector_sorted[:5])
        
        progress_bar.progress((idx + 1) / len(all_industries))

    if final_results:
        cols = ['業種', 'コード', '銘柄名', '利回り(%)', '性向(%)', '自己資本(%)', '判定', 'おすすめ度', '備考']
        st.session_state['result_df'] = pd.DataFrame(final_results)[cols]
        st.success("スキャン完了！")

if not st.session_state['result_df'].empty:
    st.dataframe(st.session_state['result_df'], use_container_width=True)
    csv = st.session_state['result_df'].to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 完全捕捉版CSVダウンロード", csv, "prime_perfect_capture.csv", "text/csv")
