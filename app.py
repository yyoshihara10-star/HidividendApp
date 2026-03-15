import streamlit as st
import pandas as pd
import yfinance as yf

st.set_page_config(page_title="プライム高配当株・真の全業種網羅", layout="wide")
st.title("高配当株スクリーニング (プライム全銘柄・時価総額順スキャン)")

if 'result_df' not in st.session_state:
    st.session_state['result_df'] = pd.DataFrame()

@st.cache_data(ttl=86400)
def fetch_jpx_prime():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        df = pd.read_excel(url)
        # プライム市場の銘柄のみを厳選
        return df[df['市場・商品区分'].str.contains('プライム')]
    except:
        return pd.DataFrame()

def analyze_stock_fixed(symbol, industry):
    stock = yf.Ticker(symbol)
    info = stock.info
    
    # 1. 配当利回り 3%未満は絶対除外
    price = info.get('currentPrice') or info.get('previousClose') or 1.0
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    dy = round((div_rate / price * 100), 2)
    if dy < 3.0: return None

    # スコアリング（5点満点）
    score = 5
    reasons = []
    
    # 2. 売上・利益の連続成長 (金融・商社等の多様な項目名に対応)
    financials = stock.financials
    # yfinanceの財務諸表から売上高に近い項目を幅広く探索
    rev_keys = ['Total Revenue', 'Operating Revenue', 'Revenue', 'Operating Income']
    growth_found = False
    for k in rev_keys:
        if k in financials.index:
            vals = pd.to_numeric(financials.loc[k], errors='coerce').dropna().values[:3]
            if len(vals) >= 2 and vals[0] < vals[1]: # 直近が前期を下回る＝減収
                score -= 1; reasons.append("売上/利益減")
            growth_found = True; break
    if not growth_found: reasons.append("成長データ不明")

    # 3. 配当性向 (30-70%目安)
    payout = info.get('payoutRatio', 0)
    if payout <= 1.0: payout *= 100
    if not (30 <= payout <= 70):
        score -= 1; reasons.append(f"性向({round(payout)}%)")

    # 4. 自己資本比率 (40%目安 / 金融免除)
    bs = stock.balance_sheet
    eq_ratio = 0.0
    is_finance = any(x in industry for x in ['銀行', '保険', '証券', 'その他金融'])
    if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
        try:
            eq_ratio = round((bs.loc['Stockholders Equity'].iloc[0] / bs.loc['Total Assets'].iloc[0]) * 100, 1)
            if not is_finance and eq_ratio < 40:
                score -= 1; reasons.append(f"財務({eq_ratio}%)")
        except: pass

    # 5. 増配・減配履歴
    try:
        divs = stock.dividends
        if not divs.empty:
            y_div = divs.resample('Y').sum().tail(3)
            if len(y_div) >= 2 and y_div.iloc[-1] < y_div.iloc[-2]:
                score -= 1; reasons.append("減配履歴")
    except: pass

    star_score = max(1, score)
    judge = "〇" if star_score >= 4 else ("△" if star_score >= 2 else "×")
    
    return {
        '利回り(%)': dy, '性向(%)': round(payout, 1), '自己資本(%)': eq_ratio,
        '判定': judge, 'おすすめ度': "★" * star_score + "☆" * (5 - star_score),
        '備考': " / ".join(reasons) if reasons else "良好(指標クリア)",
        'score': star_score
    }

if st.button("🚀 プライム全33業種・徹底スキャン", type="primary"):
    jpx_df = fetch_jpx_prime()
    if jpx_df.empty: st.stop()

    all_industries = sorted(jpx_df['33業種区分'].unique())
    final_results = []
    status_text = st.empty()
    progress_bar = st.progress(0)

    for idx, industry in enumerate(all_industries):
        status_text.text(f"業界分析中: {industry}")
        
        # 業界内の全銘柄を取得（ここが重要）
        sector_all_members = jpx_df[jpx_df['33業種区分'] == industry]
        
        # --- 業界全銘柄を対象に時価総額を確認 ---
        sector_mcap_list = []
        for _, row in sector_all_members.iterrows():
            code = f"{row['コード']}.T"
            try:
                # 高速取得用プロパティを使用
                s = yf.Ticker(code)
                m_cap = s.fast_info.get('market_cap', 0)
                sector_mcap_list.append({'code': code, 'm_cap': m_cap, 'name': row['銘柄名']})
            except: continue
        
        # 業界内の全銘柄を時価総額順に並べ、トップ15社を抽出
        top_15_by_cap = sorted(sector_mcap_list, key=lambda x: x['m_cap'], reverse=True)[:15]
        
        sector_candidates = []
        for item in top_15_by_cap:
            res = analyze_stock_fixed(item['code'], industry)
            if res:
                res.update({'業種': industry, 'コード': item['code'].replace('.T',''), '銘柄名': item['name']})
                sector_candidates.append(res)
        
        if sector_candidates:
            # スコア（星の数）順 ＞ 利回り順 で上位5社を最終リストへ
            final_results.extend(sorted(sector_candidates, key=lambda x: (-x['score'], -x['利回り(%)']))[:5])
        
        progress_bar.progress((idx + 1) / len(all_industries))

    if final_results:
        # 項目の並びを「業種・コード・銘柄名」左端に固定
        cols = ['業種', 'コード', '銘柄名', '利回り(%)', '性向(%)', '自己資本(%)', '判定', 'おすすめ度', '備考']
        st.session_state['result_df'] = pd.DataFrame(final_results)[cols]
        st.success("スキャン完了！")

if not st.session_state['result_df'].empty:
    st.dataframe(st.session_state['result_df'], use_container_width=True)
    csv = st.session_state['result_df'].to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 完全版CSVダウンロード", csv, "prime_full_scan_dividend.csv", "text/csv")
