import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="完全自動 高配当株チェッカー", layout="wide")
st.title("🎯 究極の全自動！厳格＆柔軟比較 高配当株選定")
st.markdown("JPX最新データと過去財務データを連携。各業種トップ3社を必ず抽出し、条件未達の場合はその理由を比較提示します。")

# --- セッションステート（画面リセット対策） ---
# データを保持するための領域を作成します
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

# --- 過去データの判定用ヘルパー関数 ---
def check_consecutive_growth(financials_df, row_name, years=3):
    try:
        if row_name not in financials_df.index:
            return False
        values = financials_df.loc[row_name].dropna().values[:years][::-1]
        if len(values) < years:
            return False
        return all(values[i] <= values[i+1] for i in range(len(values)-1)) # 維持または成長
    except:
        return False

def check_stable_dividends(dividends_series, target_years=10):
    try:
        if dividends_series.empty:
            return False, 0
        yearly_div = dividends_series.resample('Y').sum()
        recent_divs = yearly_div.tail(target_years).values
        actual_years = len(recent_divs)
        if actual_years < 2:
            return False, actual_years
        is_stable = all(recent_divs[i] <= recent_divs[i+1] for i in range(actual_years-1))
        return is_stable, actual_years
    except:
        return False, 0

# --- メイン処理 ---
st.sidebar.header("⚙️ 検索条件")
target_scope = st.sidebar.radio(
    "調査対象（※API制限を避けるためTOPIX500推奨）",
    ("TOPIX Core30 & Large70 (約100社)", "TOPIX 500 (約500社)")
)

st.sidebar.markdown("---")
min_yield = st.sidebar.number_input("最低配当利回り (%)", min_value=0.0, value=3.0, step=0.1)
min_payout = st.sidebar.slider("配当性向の下限 (%)", min_value=0, max_value=100, value=30)
max_payout = st.sidebar.slider("配当性向の上限 (%)", min_value=0, max_value=100, value=60)

# 実行ボタン
if st.button("🚀 ロジック実行（全自動スクリーニング）", type="primary"):
    
    # 実行のたびに前回のデータをクリア
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
        
        # ---------------------------------------------------------
        # Step 1: 基礎データ取得（ここでは足切りせず、全件の利回りを取る）
        # ---------------------------------------------------------
        status_text.info(f"⚡ 第1段階: {len(tickers)}銘柄の基礎データを高速取得中...")
        fast_results = []
        my_bar = st.progress(0)
        
        for i, ticker in enumerate(tickers):
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                div_yield = info.get('dividendYield', 0)
                
                if div_yield:
                    fast_results.append({
                        'コード': ticker,
                        '銘柄名': info.get('shortName', ticker),
                        '業種': tickers_dict[ticker],
                        '配当利回り': round(div_yield * 100, 2),
                    })
            except:
                pass
            my_bar.progress((i + 1) / len(tickers))
            time.sleep(0.05)
            
        fast_df = pd.DataFrame(fast_results)
        
        # 業種ごとに、とりあえず利回りが高い上位5社を「候補」として抽出
        candidate_df = fast_df.sort_values(by=['業種', '配当利回り'], ascending=[True, False]).groupby('業種').head(5)
        candidate_tickers = candidate_df['コード'].tolist()
        
        # ---------------------------------------------------------
        # Step 2: 候補銘柄の深掘りチェック＆理由判定
        # ---------------------------------------------------------
        status_text.info(f"🔍 第2段階: 各業種上位候補（計{len(candidate_tickers)}社）の過去履歴を解析中...")
        final_results = []
        my_bar.progress(0)
        
        for i, ticker in enumerate(candidate_tickers):
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                financials = stock.financials
                balance_sheet = stock.balance_sheet
                dividends = stock.dividends
                
                # 基本指標
                div_yield_val = info.get('dividendYield', 0)
                div_yield_pct = round(div_yield_val * 100, 2) if div_yield_val else 0.0
                payout = info.get('payoutRatio', 0)
                payout_pct = round(payout * 100, 2) if payout else 0.0
                eps_growth = info.get('earningsQuarterlyGrowth', 0)
                
                # 深掘り指標
                rev_growth = check_consecutive_growth(financials, 'Total Revenue', 3)
                op_growth = check_consecutive_growth(financials, 'Operating Income', 3) or check_consecutive_growth(financials, 'Operating Profit', 3)
                stable_div, div_years = check_stable_dividends(dividends, 10)
                
                equity_ratio = 0
                if 'Stockholders Equity' in balance_sheet.index and 'Total Assets' in balance_sheet.index:
                    try:
                        equity = balance_sheet.loc['Stockholders Equity'].iloc[0]
                        assets = balance_sheet.loc['Total Assets'].iloc[0]
                        equity_ratio = round((equity / assets) * 100, 1)
                    except:
                        pass
                
                # 厳格チェック＆理由出し
                reasons = []
                if div_yield_pct < min_yield: reasons.append(f"利回り({div_yield_pct}%)")
                if not (min_payout <= payout_pct <= max_payout): reasons.append(f"配当性向({payout_pct}%)")
                if not rev_growth: reasons.append("売上成長")
                if not op_growth: reasons.append("営利成長")
                if equity_ratio < 40.0: reasons.append(f"自己資本({equity_ratio}%)")
                if eps_growth <= 0: reasons.append("EPS減")
                if not stable_div:
                    if div_years < 2: reasons.append("配当履歴不足")
                    else: reasons.append("減配歴あり")
                
                passed = (len(reasons) == 0)
                
                if passed:
                    note = f"⭐️完全クリア(過去{div_years}年実績)"
                else:
                    note = "未達: " + ", ".join(reasons)
                    
                row = candidate_df[candidate_df['コード'] == ticker].iloc[0]
                
                final_results.append({
                    '判定': '〇' if passed else '×',
                    '銘柄コード': ticker.replace('.T', ''),
                    '銘柄名': row['銘柄名'],
                    '業種': row['業種'],
                    '配当利回り(%)': div_yield_pct,
                    '配当性向(%)': payout_pct,
                    '自己資本比率(%)': equity_ratio,
                    '配当確認年数': div_years,
                    '備考 (未達理由など)': note,
                    'is_passed': passed # ソート用の一時列
                })
            except Exception as e:
                pass
                
            my_bar.progress((i + 1) / len(candidate_tickers))
            time.sleep(0.1)
            
        status_text.empty()
        my_bar.empty()
        
        # ---------------------------------------------------------
        # 最終選定: 各業種3社を抽出（合格者を優先）
        # ---------------------------------------------------------
        final_df = pd.DataFrame(final_results)
        final_picks = []
        
        if not final_df.empty:
            for industry, group in final_df.groupby('業種'):
                # ①合格(True)が上、②利回りが高い順 に並び替え
                sorted_group = group.sort_values(by=['is_passed', '配当利回り(%)'], ascending=[False, False])
                # トップ3を取得
                top3 = sorted_group.head(3).copy()
                final_picks.append(top3)
                
            display_df = pd.concat(final_picks).reset_index(drop=True)
            # ソート用の一時列を削除
            display_df = display_df.drop(columns=['is_passed'])
            
            # セッションステートに保存（画面リセット対策）
            st.session_state['result_df'] = display_df
            st.success(f"🎉 スクリーニング完了！結果を提示します。")
        else:
            st.warning("候補銘柄の取得に失敗しました。")

# --- 結果の表示とダウンロード ---
# セッションステートにデータがある場合のみ表示する（リセットされても残る）
if not st.session_state['result_df'].empty:
    df_show = st.session_state['result_df'].copy()
    
    # リストの付番を0からではなく1からにする
    df_show.index = df_show.index + 1
    
    st.dataframe(df_show, use_container_width=True)
    
    # エクセルの文字化けを防ぐため「utf-8-sig (BOM付き)」でエンコード
    csv = df_show.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label="📥 結果をCSVでダウンロード（Excel文字化け対策済）",
        data=csv,
        file_name='dividend_stocks.csv',
        mime='text/csv'
    )
