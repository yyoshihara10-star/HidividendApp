import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="高配当株スクリーニングツール", layout="wide")
st.title("高配当株スクリーニングツール")

st.markdown("""
NISAでの中長期的な買い付け候補を抽出するための、厳格なファンダメンタルズ分析ツールです。

**【スクリーニングロジック】**
* 基礎フィルタ: 指定した配当利回りを満たしているか
* 成長性・財務: 過去3年の売上および営業利益が連続成長しているか
* 還元姿勢: 配当性向が指定範囲内であること、過去10年間で減配がないこと
* 業種別考慮: 自己資本比率40%以上（※銀行・リース等金融系は免除）、EPS成長率は参考値として評価
* 最終抽出: 全条件クリアを最上位とし、未達の場合は「未達項目が少ない順」に並び替え、各業種最大3社を抽出
""")

if 'result_df' not in st.session_state:
    st.session_state['result_df'] = pd.DataFrame()

@st.cache_data(ttl=86400)
def fetch_jpx_stock_list():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        return pd.read_excel(url)
    except Exception as e:
        st.error(f"JPXデータの取得に失敗しました: {e}")
        return pd.DataFrame()

def check_growth_detailed(financials_df, row_names, years=3, item_name=""):
    try:
        target_row = None
        for name in (row_names if isinstance(row_names, list) else [row_names]):
            if name in financials_df.index:
                target_row = name
                break
        
        if not target_row:
            return False, f"{item_name}データ無"

        row_data = pd.to_numeric(financials_df.loc[target_row], errors='coerce').dropna()
        if len(row_data) < years:
            return False, f"{item_name}データ不足"
        
        values = row_data.values[:years]
        dates = row_data.index[:years]
        
        for i in range(years - 1):
            newer_val = values[i]
            older_val = values[i+1]
            if newer_val < older_val:
                n_year = dates[i].year if hasattr(dates[i], 'year') else "直近"
                o_year = dates[i+1].year if hasattr(dates[i+1], 'year') else "過去"
                n_str = f"{newer_val/1e8:.0f}億" if abs(newer_val) >= 1e8 else f"{newer_val:.0f}"
                o_str = f"{older_val/1e8:.0f}億" if abs(older_val) >= 1e8 else f"{older_val:.0f}"
                return False, f"{item_name}減({o_year}年 {o_str} → {n_year}年 {n_str})"
        return True, ""
    except Exception:
        return False, f"{item_name}判定不可"

def check_stable_dividends_detailed(dividends_series, target_years=10):
    try:
        if dividends_series is None or dividends_series.empty:
            return False, 0, "配当データ無"
        
        yearly_div = pd.to_numeric(dividends_series, errors='coerce').dropna().resample('Y').sum()
        recent_divs = yearly_div.tail(target_years)
        actual_years = len(recent_divs)
        
        if actual_years < 2:
            return False, actual_years, f"配当履歴不足(過去{actual_years}年分)"
            
        values = recent_divs.values
        dates = recent_divs.index
        
        for i in range(actual_years - 1):
            older_val = values[i]
            newer_val = values[i+1]
            if newer_val < older_val - 0.01:
                o_year = dates[i].year
                n_year = dates[i+1].year
                return False, actual_years, f"{n_year}年減配"
                
        return True, actual_years, ""
    except Exception:
        return False, 0, "配当判定不可"

st.sidebar.header("⚙️ 検索条件")
target_scope = st.sidebar.radio(
    "調査対象",
    ("TOPIX Core30 & Large70 (約100社)", "TOPIX 500 (約500社)")
)

st.sidebar.markdown("---")
min_yield = st.sidebar.number_input("最低配当利回り (%)", min_value=0.0, value=3.0, step=0.1)
min_payout = st.sidebar.slider("配当性向の下限 (%)", min_value=0, max_value=100, value=30)
max_payout = st.sidebar.slider("配当性向の上限 (%)", min_value=0, max_value=100, value=60)

if st.button("🚀 ロジック実行（全自動スクリーニング）", type="primary"):
    st.session_state['result_df'] = pd.DataFrame()
    status_text = st.empty()
    status_text.info("📥 JPXから最新の上場銘柄データを取得中...")
    
    jpx_df = fetch_jpx_stock_list()
    
    if not jpx_df.empty:
        if "500" in target_scope:
            target_df = jpx_df[jpx_df['規模区分'].isin(['TOPIX Core30', 'TOPIX Large70', 'TOPIX Mid400'])]
        else:
            target_df = jpx_df[jpx_df['規模区分'].isin(['TOPIX Core30', 'TOPIX Large70'])]
            
        tickers_dict = {f"{row['コード']}.T": {'name': row['銘柄名'], 'industry': row['33業種区分']} for _, row in target_df.iterrows()}
        tickers = list(tickers_dict.keys())
        
        status_text.info(f"⚡ 第1段階: {len(tickers)}銘柄の利回りをチェック中...")
        candidate_tickers = []
        my_bar = st.progress(0)
        
        for i, ticker in enumerate(tickers):
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                
                raw_yield = info.get('dividendYield') or info.get('trailingAnnualDividendYield', 0)
                div_yield_pct = raw_yield if raw_yield > 1 else raw_yield * 100
                
                if div_yield_pct >= min_yield:
                    candidate_tickers.append(ticker)
            except:
                pass
            my_bar.progress((i + 1) / len(tickers))
            time.sleep(0.02)
            
        my_bar.empty()
        
        status_text.info(f"🔍 第2段階: 基礎をクリアした候補（計{len(candidate_tickers)}社）の詳細解析中...")
        final_results = []
        my_bar = st.progress(0)
        
        # 金融系セクターの定義
        financial_sectors = ['銀行業', '証券、商品先物取引業', '保険業', 'その他金融業']
        
        for i, ticker in enumerate(candidate_tickers):
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                financials = stock.financials
                balance_sheet = stock.balance_sheet
                dividends = stock.dividends
                industry = tickers_dict[ticker]['industry']
                
                raw_yield = info.get('dividendYield') or info.get('trailingAnnualDividendYield', 0)
                div_yield_pct = round(raw_yield if raw_yield > 1 else raw_yield * 100, 2)
                
                raw_payout = info.get('payoutRatio', 0)
                payout_pct = round(raw_payout if raw_payout > 1 else raw_payout * 100, 2)
                
                eps_growth = info.get('earningsQuarterlyGrowth', None)
                eps_pct = round(eps_growth * 100, 1) if eps_growth is not None else None
                
                rev_ok, rev_reason = check_growth_detailed(financials, ['Total Revenue', 'Operating Revenue', 'Revenue'], 3, "売上")
                op_ok, op_reason = check_growth_detailed(financials, ['Operating Income', 'Operating Profit', 'EBIT', 'Net Income'], 3, "利益")
                div_ok, div_years, div_reason = check_stable_dividends_detailed(dividends, 10)
                
                equity_ratio = 0.0
                if 'Stockholders Equity' in balance_sheet.index and 'Total Assets' in balance_sheet.index:
                    try:
                        equity = balance_sheet.loc['Stockholders Equity'].iloc[0]
                        assets = balance_sheet.loc['Total Assets'].iloc[0]
                        equity_ratio = round((equity / assets) * 100, 1)
                    except:
                        pass
                
                reasons = []
                refs = [] # 参考情報の格納用
                
                if payout_pct < min_payout or payout_pct > max_payout:
                    reasons.append(f"配当性向({payout_pct}%)")
                if not rev_ok: reasons.append(rev_reason)
                if not op_ok: reasons.append(op_reason)
                if not div_ok: reasons.append(div_reason)
                
                # 自己資本比率の判定（金融系は免除し参考値へ）
                if equity_ratio > 0 and equity_ratio < 40.0:
                    if industry in financial_sectors:
                        refs.append(f"自己資本({equity_ratio}%)")
                    else:
                        reasons.append(f"自己資本({equity_ratio}%)")
                elif equity_ratio == 0.0:
                    refs.append("自己資本データ無")
                
                # EPSの判定（全業種で参考値へ）
                if eps_pct is not None and eps_pct < 0:
                    refs.append(f"EPS減({eps_pct}%)")
                elif eps_pct is None:
                    refs.append("EPSデータ無")
                
                fail_count = len(reasons)
                passed = (fail_count == 0)
                
                note = f"⭐️クリア(過去{div_years}年実績)" if passed else "未達: " + " / ".join(reasons)
                if refs:
                    note += f" [参考: {' , '.join(refs)}]"
                
                final_results.append({
                    '判定': '〇' if passed else '×',
                    '銘柄コード': ticker.replace('.T', ''),
                    '銘柄名': tickers_dict[ticker]['name'],
                    '業種': industry,
                    '配当利回り(%)': div_yield_pct,
                    '配当性向(%)': payout_pct,
                    '自己資本比率(%)': equity_ratio,
                    'EPS成長率(%)': eps_pct if eps_pct is not None else 0.0,
                    '配当確認年数': div_years,
                    '備考 (詳細)': note,
                    'fail_count': fail_count
                })
            except Exception:
                pass
                
            my_bar.progress((i + 1) / len(candidate_tickers))
            time.sleep(0.1)
            
        status_text.empty()
        my_bar.empty()
        
        final_df = pd.DataFrame(final_results)
        final_picks = []
        
        if not final_df.empty:
            for industry, group in final_df.groupby('業種'):
                sorted_group = group.sort_values(by=['fail_count', '配当利回り(%)'], ascending=[True, False])
                top3 = sorted_group.head(3).copy()
                final_picks.append(top3)
                
            display_df = pd.concat(final_picks).reset_index(drop=True)
            display_df = display_df.drop(columns=['fail_count'])
            
            st.session_state['result_df'] = display_df
            st.success(f"🎉 スクリーニング完了！")
        else:
            st.warning("条件に合致する候補銘柄が見つかりませんでした。")

if not st.session_state['result_df'].empty:
    df_show = st.session_state['result_df'].copy()
    df_show.index = df_show.index + 1
    
    st.dataframe(df_show, use_container_width=True)
    
    csv = df_show.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label="📥 結果をCSVでダウンロード",
        data=csv,
        file_name='dividend_stocks.csv',
        mime='text/csv'
    )
