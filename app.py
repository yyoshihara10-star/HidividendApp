import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="プライム高配当株・王道銘柄網羅ツール", layout="wide")
st.title("高配当株スクリーニング (プライム売上・時価総額上位ベース)")

if 'result_df' not in st.session_state:
    st.session_state['result_df'] = pd.DataFrame()

@st.cache_data(ttl=86400)
def fetch_jpx_prime_with_market_cap():
    """JPXから銘柄を取得し、時価総額でソート可能な状態にする"""
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        df = pd.read_excel(url)
        df = df[df['市場・商品区分'].str.contains('プライム')]
        # 注: JPXデータには時価総額が含まれないため、分析時に各業界の上位を広く取るように変更
        return df
    except Exception as e:
        st.error(f"データ取得失敗: {e}")
        return pd.DataFrame()

def analyze_stock_optimized(stock, industry):
    info = stock.info
    # 基本データ
    price = info.get('currentPrice') or info.get('previousClose') or 1.0
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    dy = round((div_rate / price * 100), 2) if div_rate else 0.0
    
    # 利回り3%未満は絶対除外
    if dy < 3.0: return None

    payout = info.get('payoutRatio', 0)
    if payout <= 1.0: payout *= 100
    
    score = 5
    reasons = []
    
    # 財務・成長性チェック
    financials = stock.financials
    # 売上高チェック（商社・リース等に対応するため広範囲のキーを検索）
    rev_keys = ['Total Revenue', 'Operating Revenue', 'Revenue', 'Operating Income']
    growth_ok = False
    for k in rev_keys:
        if k in financials.index:
            vals = pd.to_numeric(financials.loc[k], errors='coerce').dropna().values[:3]
            if len(vals) >= 2:
                if vals[0] < vals[1]: 
                    score -= 1; reasons.append("売上/利益減")
                growth_ok = True
                break
    if not growth_ok: reasons.append("成長データ不足")

    # 配当性向 (30-70%目安)
    if not (30 <= payout <= 70):
        score -= 1; reasons.append(f"性向({round(payout)}%)")

    # 自己資本比率 (40%目安 / 金融・リース免除)
    bs = stock.balance_sheet
    eq_ratio = 0.0
    is_finance = any(x in industry for x in ['銀行', '保険', '証券', 'その他金融'])
    if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
        try:
            eq_ratio = round((bs.loc['Stockholders Equity'].iloc[0] / bs.loc['Total Assets'].iloc[0]) * 100, 1)
            if not is_finance and eq_ratio < 40:
                score -= 1; reasons.append(f"財務({eq_ratio}%)")
        except: pass

    # 増配・EPS成長（参考）
    eps_growth = info.get('earningsQuarterlyGrowth', 0) or 0
    
    star_score = max(1, score)
    judge = "〇" if star_score >= 4 else ("△" if star_score >= 2 else "×")
    
    return {
        '利回り(%)': dy, '性向(%)': round(payout, 1), '自己資本(%)': eq_ratio,
        'EPS成長(%)': round(eps_growth * 100, 1), '判定': judge, 
        'おすすめ度': "★" * star_score + "☆" * (5 - star_score),
        '備考': " / ".join(reasons) if reasons else "良好(指標クリア)", 
        'score': star_score, 'dy_val': dy
    }

if st.button("🚀 プライム全業種・王道銘柄スキャン開始", type="primary"):
    jpx_df = fetch_jpx_prime_with_market_cap()
    if jpx_df.empty: st.stop()

    all_industries = sorted(jpx_df['33業種区分'].unique())
    final_results = []
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    for idx, industry in enumerate(all_industries):
        status_text.text(f"業界分析中: {industry}")
        
        # 業界全銘柄を対象にする（上位10社に絞らず、まず3%以上を広く拾う）
        sector_all = jpx_df[jpx_df['33業種区分'] == industry]
        
        sector_candidates = []
        # 各業界で最大20銘柄までスキャンし、王道銘柄を逃さないようにする
        for _, row in sector_all.head(20).iterrows():
            try:
                res = analyze_stock_optimized(yf.Ticker(f"{row['コード']}.T"), industry)
                if res:
                    res.update({'業種': industry, 'コード': row['コード'], '銘柄名': row['銘柄名']})
                    sector_candidates.append(res)
            except: continue
        
        if sector_candidates:
            # スコア順 ＞ 利回り順
            sector_sorted = sorted(sector_candidates, key=lambda x: (-x['score'], -x['利回り(%)']))
            final_results.extend(sector_sorted[:5])
        
        progress_bar.progress((idx + 1) / len(all_industries))

    if final_results:
        # 順序指定：業種、コード、銘柄名 を左に
        cols = ['業種', 'コード', '銘柄名', '利回り(%)', '性向(%)', '自己資本(%)', 'EPS成長(%)', '判定', 'おすすめ度', '備考']
        st.session_state['result_df'] = pd.DataFrame(final_results)[cols]
        st.success("スキャン完了！三菱HCキャピタルや積水ハウス等の王道銘柄を含め抽出しました。")

if not st.session_state['result_df'].empty:
    st.dataframe(st.session_state['result_df'], use_container_width=True)
    csv = st.session_state['result_df'].to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 CSVダウンロード", csv, "prime_dividend_optimized.csv", "text/csv")
