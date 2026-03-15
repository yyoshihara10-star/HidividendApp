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
        return df[df['市場・商品区分'].str.contains('プライム')]
    except:
        return pd.DataFrame()

def check_payout_recovery(info):
    """
    配当性向が70%超の場合、一時的かどうかを予想EPSで判定。
    戻り値: (is_recovery: bool, recovery_note: str)
    """
    trailing_eps = info.get('trailingEps')
    forward_eps  = info.get('forwardEps')

    # 予想EPSが存在し、かつ実績EPSより改善している場合を「回復見込み」とみなす
    if (
        trailing_eps is not None and forward_eps is not None
        and trailing_eps != 0
        and forward_eps > trailing_eps
    ):
        improvement = round((forward_eps - trailing_eps) / abs(trailing_eps) * 100, 1)
        note = f"業績回復見込み(予EPS+{improvement}%)"
        return True, note

    # アナリスト推奨が Buy/Strong Buy 系なら補助的に回復シグナルとして使用
    recommendation = (info.get('recommendationKey') or '').lower()
    if recommendation in ('buy', 'strong_buy'):
        note = "業績回復見込み(アナリスト買い推奨)"
        return True, note

    return False, ""

def analyze_stock_final_retry(symbol, industry):
    stock = yf.Ticker(symbol)
    info  = stock.info

    # 基本データ
    price   = info.get('currentPrice') or info.get('previousClose') or 1.0
    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate', 0)
    dy = round((div_rate / price * 100), 2)

    # 利回り3%未満は除外
    if dy < 3.0:
        return None

    score   = 5
    reasons = []

    # ── 売上/利益トレンド ─────────────────────────────
    financials  = stock.financials
    rev_keys    = ['Total Revenue', 'Operating Revenue', 'Revenue', 'Operating Income']
    growth_found = False
    for k in rev_keys:
        if k in financials.index:
            vals = pd.to_numeric(financials.loc[k], errors='coerce').dropna().values[:3]
            if len(vals) >= 2 and vals[0] < vals[1]:
                score -= 1
                reasons.append("売上/利益減")
            growth_found = True
            break
    if not growth_found:
        reasons.append("成長データ不明")

    # ── 配当性向チェック（★ 変更箇所） ──────────────────
    payout = info.get('payoutRatio', 0)
    if payout <= 1.0:
        payout *= 100  # 小数表記を%に変換

    if payout > 70:
        is_recovery, recovery_note = check_payout_recovery(info)
        if not is_recovery:
            # 回復見込みなし → 除外
            return None
        else:
            # 回復見込みあり → スコア減点＋備考に補足して継続
            score -= 1
            reasons.append(f"性向({round(payout)}%・一時的)")
            reasons.append(recovery_note)
    elif payout < 30:
        # 性向が低すぎる場合も減点（配当余力の過大）
        score -= 1
        reasons.append(f"性向({round(payout)}%・低)")
    # 30〜70% の場合は正常範囲としてそのまま通過

    # ── 自己資本比率 ──────────────────────────────────
    bs = stock.balance_sheet
    eq_ratio = 0.0
    is_finance = any(x in industry for x in ['銀行', '保険', '証券', 'その他金融'])
    if 'Stockholders Equity' in bs.index and 'Total Assets' in bs.index:
        try:
            eq_ratio = round(
                (bs.loc['Stockholders Equity'].iloc[0] /
                 bs.loc['Total Assets'].iloc[0]) * 100, 1
            )
            if not is_finance and eq_ratio < 40:
                score -= 1
                reasons.append(f"財務({eq_ratio}%)")
        except:
            pass

    star_score = max(1, score)
    judge      = "〇" if star_score >= 4 else ("△" if star_score >= 2 else "×")

    return {
        '利回り(%)':   dy,
        '性向(%)':     round(payout, 1),
        '自己資本(%)': eq_ratio,
        '判定':        judge,
        'おすすめ度':  "★" * star_score + "☆" * (5 - star_score),
        '備考':        " / ".join(reasons) if reasons else "良好(指標クリア)",
        'score':       star_score,
        'm_cap':       info.get('marketCap', 0),
    }

if st.button("🚀 プライム全銘柄・徹底スキャン", type="primary"):
    jpx_df = fetch_jpx_prime()
    if jpx_df.empty:
        st.stop()

    all_industries = sorted(jpx_df['33業種区分'].unique())
    final_results  = []
    status_text    = st.empty()
    progress_bar   = st.progress(0)

    for idx, industry in enumerate(all_industries):
        status_text.text(f"【最優先スキャン】業界: {industry}")
        sector_members    = jpx_df[jpx_df['33業種区分'] == industry]
        sector_candidates = []

        for _, row in sector_members.iterrows():
            code = f"{row['コード']}.T"
            try:
                res = analyze_stock_final_retry(code, industry)
                if res:
                    res.update({
                        '業種':   industry,
                        'コード': row['コード'],
                        '銘柄名': row['銘柄名'],
                    })
                    sector_candidates.append(res)
            except:
                continue

        if sector_candidates:
            sector_sorted = sorted(sector_candidates, key=lambda x: x['m_cap'], reverse=True)
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
```

---

### 追加した `check_payout_recovery()` の判定ロジック
```
性向 > 70% の銘柄
      │
      ├─ 予想EPS (forwardEps) > 実績EPS (trailingEps)
      │       → 回復見込みあり ✅
      │         備考に「業績回復見込み(予EPS+XX%)」を記載
      │
      ├─ アナリスト推奨が buy / strong_buy
      │       → 回復見込みあり ✅（EPSデータがない場合の補助）
      │         備考に「業績回復見込み(アナリスト買い推奨)」を記載
      │
      └─ どちらも該当しない
              → 除外 ❌（return None）
