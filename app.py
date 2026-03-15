import streamlit as st
import pandas as pd
import yfinance as yf
import time

st.set_page_config(page_title="完全自動 高配当株チェッカー", layout="wide")
st.title("🎯 究極の全自動！厳格＆柔軟比較 高配当株選定")
st.markdown("JPX最新データと過去財務データを連携。データ不足による機会損失を防ぎ、10年実績完備の銘柄と比較提示します。")

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
    """過去指定年数の連続成長（右肩上がり）を判定"""
    try:
        if row_name not in financials_df.index:
            return False
        values = financials_df.loc[row_name].dropna().values[:years][::-1]
        if len(values) < years:
            return False
        return all(values[i] < values[i+1] for i in range(len(values)-1))
    except:
        return False

def check_stable_dividends(dividends_series, target_years=10):
    """
    過去指定年数（10年）減配していないか判定。
    10年取れない場合は、取れた年数（最低2年）で判定し、確認できた年数も返す。
    """
    try:
        if dividends_series.empty:
            return False, 0
        
        # 'Y'（年次）でリサンプルして合計
        yearly_div = dividends_series.resample('Y').sum()
        recent_divs = yearly_div.tail(target_years).values
        actual_years = len(recent_divs)
        
        # 比較には最低2年のデータが必要
        if actual_years < 2:
            return False, actual_years
            
        # 取得できた期間内で前年割れ（減配）がないかチェック
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

if st.button("🚀 ロジック実行（全自動スクリーニング）", type="primary"):
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
        # Step 1: 基礎データ取得と高速足切り
        # ---------------------------------------------------------
        status_text.info(f"⚡ 第1段階: {len(tickers)}銘柄の基礎データを高速取得中...")
        fast_results = []
        my_bar = st.progress(0)
        
        for i, ticker in enumerate(tickers):
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                
                div_yield = info.get('dividendYield', 0)
                payout_ratio = info.get('payoutRatio', 0)
                revenue = info.get('totalRevenue', 0)
                
                if div_yield and payout_ratio and revenue:
                    fast_results.append({
                        'コード': ticker,
                        '銘柄名': info.get('shortName', ticker),
                        '業種': tickers_dict[ticker],
                        '売上高': revenue,
                        '配当利回り': round(div_yield * 100, 2),
                        '配当性向': round(payout_ratio * 100, 2),
                        'EPS成長率': info.get('earningsQuarterlyGrowth', 0)
                    })
            except:
                pass
            my_bar.progress((i + 1) / len(tickers))
            time.sleep(0.05)
            
        fast_df = pd.DataFrame(fast_results)
        
        filtered_df = fast_df[
            (fast_df['配当利回り'] >= min_yield) & 
            (fast_df['配当性向'] >= min_payout) & 
            (fast_df['配当性向'] <= max_payout)
        ]
        
        top3_df = filtered_df.sort_values(by=['業種', '売上高'], ascending=[True, False]).groupby('業種').head(3)
        candidate_tickers = top3_df['コード'].tolist()
        
        # ---------------------------------------------------------
        # Step 2 & 3: 財務履歴の深掘りチェック
        # ---------------------------------------------------------
        status_text.info(f"🔍 第2段階: 候補（計{len(candidate_tickers)}社）の過去履歴を解析中...")
        final_results = []
        my_bar.progress(0)
        
        for i, ticker in enumerate(candidate_tickers):
            try:
                stock = yf.Ticker(ticker)
                financials = stock.financials
                balance_sheet = stock.balance_sheet
                dividends = stock.dividends
                
                rev_growth = check_consecutive_growth(financials, 'Total Revenue', 3)
                op_growth = check_consecutive_growth(financials, 'Operating Income', 3) or check_consecutive_growth(financials, 'Operating Profit', 3)
                
                equity_ratio = 0
                if 'Stockholders Equity' in balance_sheet.index and 'Total Assets' in balance_sheet.index:
                    equity = balance_sheet.loc['Stockholders Equity'].iloc[0]
                    assets = balance_sheet.loc['Total Assets'].iloc[0]
                    equity_ratio = round((equity / assets) * 100, 1)
                
                # 安定配当の判定（取れた年数も取得）
                stable_div, div_years = check_stable_dividends(dividends, 10)
                
                row = top3_df[top3_df['コード'] == ticker].iloc[0]
                
                if rev_growth and op_growth and stable_div and (equity_ratio >= 40.0) and (row['EPS成長率'] > 0):
                    
                    # 備考欄の作成
                    if div_years >= 10:
                        note = "10年減配なし(完全クリア)"
                    else:
                        note = f"データ不足(過去{div_years}年は減配なし)"
                        
                    final_results.append({
                        '銘柄コード': ticker.replace('.T', ''),
                        '銘柄名': row['銘柄名'],
                        '業種': row['業種'],
                        '配当利回り(%)': row['配当利回り'],
                        '配当性向(%)': row['配当性向'],
                        '自己資本比率(%)': equity_ratio,
                        '配当確認年数': div_years,
                        '備考': note
                    })
            except Exception as e:
                pass
                
            my_bar.progress((i + 1) / len(candidate_tickers))
            time.sleep(0.2)
            
        status_text.empty()
        my_bar.empty()
        
        # ---------------------------------------------------------
        # 最終選定: 業種ごとに比較・抽出
        # ---------------------------------------------------------
        final_df = pd.DataFrame(final_results)
        final_picks = []
        
        if not final_df.empty:
            for industry, group in final_df.groupby('業種'):
                # 業種内で利回りが高い順にソート
                group = group.sort_values(by='配当利回り(%)', ascending=False)
                
                # まず、その業種で一番利回りが高い銘柄を取得
                top_stock = group.iloc[0].copy()
                final_picks.append(top_stock)
                
                # もしトップの銘柄の配当確認年数が10年未満だった場合
                if top_stock['配当確認年数'] < 10:
                    # 同じ業種内で、10年以上のデータがある銘柄を探す
                    perfect_stocks = group[group['配当確認年数'] >= 10]
                    
                    if not perfect_stocks.empty:
                        # 10年完備の中で一番利回りが高い銘柄（次点）を取得
                        best_perfect = perfect_stocks.iloc[0].copy()
                        
                        # トップ銘柄と違う場合のみ、比較用として追加
                        if best_perfect['銘柄コード'] != top_stock['銘柄コード']:
                            best_perfect['備考'] = "【比較推奨】10年実績完備の同業種トップ"
                            final_picks.append(best_perfect)
            
            display_df = pd.DataFrame(final_picks).reset_index(drop=True)
            
            st.success(f"🎉 スクリーニング完了！抽出結果を提示します。")
            st.dataframe(display_df, use_container_width=True)
            
            csv = display_df.to_csv(index=False).encode('utf-8')
            st.download_button(label="📥 結果をCSVでダウンロード", data=csv, file_name='flexible_best_stocks.csv', mime='text/csv')
        else:
            st.warning("条件をクリアした銘柄はありませんでした。")