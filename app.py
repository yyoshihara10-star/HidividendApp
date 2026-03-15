import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="高配当株スクリーニングツール", layout="wide")
st.title("高配当株スクリーニングツール")

st.markdown("""
NISAでの中長期的な買い付け候補を抽出するための、厳格なファンダメンタルズ分析ツールです。

**【スクリーニングロジック】**
* **基礎フィルタ**: 指定した配当利回り、および配当性向の範囲内であること
* **成長性・財務**: 過去3年の売上および営業利益（または準ずる利益）が連続成長、自己資本比率40%以上、直近EPS成長率がマイナスでないこと
* **配当の安定性**: 過去10年間で減配がないこと（※データ不足の場合は取得可能な期間で判定）
* **最終抽出**: 全条件クリアを最上位とし、未達の場合は**「未達項目が少ない順（健全性優先）」**に並び替え、各業種最大3社を抽出
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
    """複数パターンの項目名に対応し、クラッシュを防ぐ安全な成長判定"""
    try:
        target_row = None
        for name in (row_names if isinstance(row_names, list) else [row_names]):
            if name in financials_df.index:
                target_row = name
                break
        
        if not target_row:
            return False, f"{item_name}データ無"

        # 確実に数値化し、欠損値（NaN）は除外する
        row_data = pd.to_numeric(financials_df.loc[target_row], errors='coerce').dropna()
        
        if len(row_data) < years:
            return False, f"{item_name}データ不足(過去{len(row_data)}年分)"
        
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
    except Exception as e:
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
            # 浮動小数点誤差を考慮
            if newer_val < older_val - 0.01:
                o_year = dates[i].year
                n_year = dates[i+1].year
                return False, actual_years, f"{n_year}年減配({older_val:.1f}円→{newer_val:.1f}円)"
                
        return True, actual_years, ""
    except Exception as e:
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
            
        tickers_dict = {f"{row['コード']}.T": row['33業種区分'] for _, row in target_df.iterrows()}
        tickers = list(tickers_dict.keys())
        
        status_text.info(f"⚡ 第1段階: {len(tickers)}銘柄の基礎データをチェック中...")
        candidate_tickers = []
        my_bar = st.progress(0)
        
        for i, ticker in enumerate(tickers):
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                
                raw_yield = info.get('dividendYield', 0)
                div_yield_pct = raw_yield if raw_yield > 1 else raw_yield * 100
                
                raw_payout = info.get('payoutRatio', 0)
                payout_pct = raw_payout if raw_payout > 1 else raw_payout * 100
                
                if (div_yield_pct >= min_yield) and (min_payout <= payout_pct <= max_payout):
                    candidate_tickers.append(ticker)
            except:
                pass
            my_bar.progress((i + 1) / len(tickers))
            time.sleep(0.02)
            
        my_bar.empty()
        
        status_text.info(f"🔍 第2段階: 基礎をクリアした候補（計{len(candidate_tickers)}社）の詳細解析中...")
        final_results = []
        my_bar = st.progress(0)
        
        for i, ticker in enumerate(candidate_tickers):
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                financials = stock.financials
                balance_sheet = stock.balance_sheet
                dividends = stock.dividends
                
                raw_yield = info.get('dividendYield', 0)
                div_yield_pct = round(raw_yield if raw_yield > 1 else raw_yield * 100, 2)
                
                raw_payout = info.get('payoutRatio', 0)
                payout_pct = round(raw_payout if raw_payout > 1 else raw_payout * 100, 2)
                
                eps_growth = info.get('earningsQuarterlyGrowth', None)
                
                # 商社対策として、利益の検索範囲をEBITやNet Incomeまで広げる
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
                if not rev_ok: reasons.append(rev_reason)
                if not op_ok: reasons.append(op_reason)
                if equity_ratio < 40.0: reasons.append(f"自己資本不足({equity_ratio}%)")
                if eps_growth is not None and eps_growth < 0: reasons.append(f"直近EPS減({eps_growth*100:.1f}%)")
                if not div_ok: reasons.append(div_reason)
                
                fail_count = len(reasons)
                passed = (fail_count == 0)
                note = f"⭐️完全クリア(過去{div_years}年実績)" if passed else "未達: " + " / ".join(reasons)
                
                final_results.append({
                    '判定': '〇' if passed else '×',
                    '銘柄コード': ticker.replace('.T', ''),
                    '銘柄名': info.get('shortName', ticker),
                    '業種': tickers_dict[ticker],
                    '配当利回り(%)': div_yield_pct,
                    '配当性向(%)': payout_pct,
                    '自己資本比率(%)': equity_ratio,
                    '配当確認年数': div_years,
                    '備考 (詳細)': note,
                    'fail_count': fail_count # ソート用の隠しスコア
                })
            except Exception as e:
                pass
                
            my_bar.progress((i + 1) / len(candidate_tickers))
            time.sleep(0.1)
            
        status_text.empty()
        my_bar.empty()
        
        final_df = pd.DataFrame(final_results)
        final_picks = []
        
        if not final_df.empty:
            for industry, group in final_df.groupby('業種'):
                # ★修正ポイント：未達項目が「少ない順（昇順）」、次に「利回り（降順）」でソート
                sorted_group = group.sort_values(by=['fail_count', '配当利回り(%)'], ascending=[True, False])
                top3 = sorted_group.head(3).copy()
                final_picks.append(top3)
                
            display_df = pd.concat(final_picks).reset_index(drop=True)
            display_df = display_df.drop(columns=['fail_count']) # 表示前にスコアを隠す
            
            st.session_state['result_df'] = display_df
            st.success(f"🎉 スクリーニング完了！健全性の高い銘柄を優先して抽出しました。")
        else:
            st.warning("条件に合致する候補銘柄が見つかりませんでした。")

if not st.session_state['result_df'].empty:
    df_show = st.session_state['result_df'].copy()
    df_show.index = df_show.index + 1
    
    st.dataframe(df_show, use_container_width=True)
    
    csv = df_show.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label="📥 結果をCSVでダウンロード（Excel文字化け対策済）",
        data=csv,
        file_name='dividend_stocks_detailed.csv',
        mime='text/csv'
    )
