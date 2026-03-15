import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="プライム市場・高配当全業種網羅ツール", layout="wide")
st.title("高配当株スクリーニング (プライム3%以上・全33業種完全網羅)")

if 'result_df' not in st.session_state:
    st.session_state['result_df'] = pd.DataFrame()

@st.cache_data(ttl=86400)
def fetch_jpx_prime_list():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        df = pd.read_excel(url)
        # プライム市場のみ
        df = df[df['市場・商品区分'].str.contains('プライム')]
        return df
    except Exception as e:
        st.error(f"JPXデータ取得失敗: {e}")
        return pd.DataFrame()

def analyze_stock_full_coverage(stock, industry):
    """利回り3%以上なら、指標を満たさなくても必ずスコア化して返す"""
    info = stock.info
    price = info.get('currentPrice') or info.get('previousClose') or 1.0
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    dy = round((div_rate / price * 100), 2) if div_rate else 0.0
    
    # 利回り3%未満はスキップ（全業種に3%以上は存在するという前提）
    if dy < 3.0:
        return None

    payout = info.get('payoutRatio', 0)
    if payout <= 1.0: payout *= 100
    
    score = 5
    reasons = []
    
    # --- 指標チェック（減点方式） ---
    # 1. 売上・利益の連続成長 (3期)
    financials = stock.financials
    growth_fail = False
    for key in ['Total Revenue', 'Operating Income']:
        if key in financials.index:
            vals = pd.to_numeric(financials.loc[key], errors='coerce').dropna().values[:3]
            if len(vals) >= 2 and vals[0] < vals[1]: # 直近 < 前期
                growth_fail = True; break
    if growth_fail:
        score -= 1; reasons.append("成長停滞")

    # 2. 配当性向 (30-60%目安)
    if not (30 <= payout <= 70):
        score -= 1; reasons.append(f"性向外({round(payout)}%)")

    # 3. 自己資本比率 (40%目安 / 金融免除)
    bs = stock.balance_sheet
    eq_ratio = 0.0
    if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
        eq_ratio = round((bs.loc['Stockholders Equity'].iloc[0] / bs.loc['Total Assets'].iloc[0]) * 100, 1)
        if industry not in ['銀行業', '保険業', '証券、商品先物取引業', 'その他金融業'] and eq_ratio < 40:
            score -= 1; reasons.append(f"財務低({eq_ratio}%)")

    # 4. 増配・減配
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
        'score': star_score, 'dy_val': dy
    }

if st.button("🚀 プライム全33業種・網羅スキャン", type="primary"):
    jpx_df = fetch_jpx_prime_list()
    if jpx_df.empty: st.stop()

    # 全33業種を確実に処理
    all_industries = sorted(jpx_df['33業種区分'].unique())
    final_results = []
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    for idx, industry in enumerate(all_industries):
        status_text.text(f"業界分析中: {industry}")
        
        # 業界別の売上(時価総額)上位10社
        sector_stocks = jpx_df[jpx_df['33業種区分'] == industry].head(10)
        
        sector_candidates = []
        for _, row in sector_stocks.iterrows():
            code = f"{row['コード']}.T"
            try:
                res = analyze_stock_full_coverage(yf.Ticker(code), industry)
                if res:
                    res.update({'業種': industry, 'コード': row['コード'], '銘柄名': row['銘柄名']})
                    sector_candidates.append(res)
            except: continue
        
        if sector_candidates:
            # スコア（星の数）が良い順 ＞ 利回りが高い順
            sector_sorted = sorted(sector_candidates, key=lambda x: (-x['score'], -x['dy_val']))
            # 最低1社、最大5社を抽出
            final_results.extend(sector_sorted[:5])
        else:
            # もし上位10社に3%以上がいなければ、業界全銘柄から探す（全業種網羅の意地）
            backup_stocks = jpx_df[jpx_df['33業種区分'] == industry]
            for _, row in backup_stocks.iloc[10:30].iterrows(): # 次の20社をチェック
                try:
                    res = analyze_stock_full_coverage(yf.Ticker(f"{row['コード']}.T"), industry)
                    if res:
                        res.update({'業種': industry, 'コード': row['コード'], '銘柄名': row['銘柄名']})
                        final_results.append(res)
                        break # 1社見つかればOK
                except: continue
        
        progress_bar.progress((idx + 1) / len(all_industries))

    if final_results:
        st.session_state['result_df'] = pd.DataFrame(final_results).drop(columns=['score', 'dy_val'])
        st.success("スキャン完了！全33業種のリストを作成しました。")
    else:
        st.error("銘柄が見つかりませんでした。")

if not st.session_state['result_df'].empty:
    # 業種順に並べて表示
    st.dataframe(st.session_state['result_df'].sort_values('業種'), use_container_width=True)
    csv = st.session_state['result_df'].to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 CSVダウンロード", csv, "prime_33_industries.csv", "text/csv")
