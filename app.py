# app.py
# 実行方法: streamlit run app.py

import streamlit as st
import pandas as pd
import sqlite3
import time

st.set_page_config(page_title="プライム高配当株スクリーニング", layout="wide")
st.title("高配当株スクリーニング (プライム全業種・全銘柄総当たり版)")

DB_PATH = "results.db"

def get_status():
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT key, value FROM scan_status").fetchall()
        conn.close()
        return dict(rows)
    except:
        return {}

def get_results():
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql("""
            SELECT
                scanned_at   AS スキャン日時,
                industry     AS 業種,
                code         AS コード,
                name         AS 銘柄名,
                yield_pct    AS '利回り(%)',
                payout_pct   AS '配当性向(%)',
                equity_pct   AS '自己資本(%)',
                mcap_oku     AS '時価総額(億)',
                judge        AS 判定,
                stars        AS おすすめ度,
                note         AS 備考,
                score
            FROM scan_results
            ORDER BY score DESC, mcap_oku DESC
        """, conn)
        conn.close()
        return df
    except:
        return pd.DataFrame()

# ── ステータス表示 ──────────────────────────────────────
status = get_status()
state  = status.get('state', 'not_started')

if state == 'running':
    progress = int(status.get('progress', 0))
    current  = status.get('current', '...')
    started  = status.get('started', '')

    st.info(f"⏳ スキャン実行中（開始: {started}）")
    st.progress(progress / 100)
    st.caption(f"現在: {current}")

    # 途中結果を表示
    df = get_results()
    if not df.empty:
        st.caption(f"途中経過: {len(df)} 銘柄取得済み（ページを更新すると最新になります）")
    
    # 自動更新ボタン
    if st.button("🔄 最新状態に更新"):
        st.rerun()

elif state == 'done':
    finished = status.get('finished', '')
    st.success(f"✅ スキャン完了（完了日時: {finished}）")

else:
    st.warning("スキャンがまだ実行されていません。")
    st.code("python worker.py", language="bash")
    st.caption("上記コマンドをターミナルで実行してください。ブラウザを閉じても動き続けます。")

# ── 結果表示 ────────────────────────────────────────────
df = get_results()

if not df.empty:
    display_df = df.drop(columns=['score', 'スキャン日時'], errors='ignore')
    industries = df['業種'].unique().tolist()
    if "商社" in industries:
        industries = ["商社"] + [i for i in industries if i != "商社"]

    tabs = st.tabs(["📋 全件"] + industries)
    with tabs[0]:
        st.dataframe(display_df, use_container_width=True)
    for tab, ind in zip(tabs[1:], industries):
        with tab:
            st.dataframe(
                display_df[display_df['業種'] == ind].reset_index(drop=True),
                use_container_width=True
            )

    csv = display_df.to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 CSVダウンロード", csv, "prime_high_dividend.csv", "text/csv")
