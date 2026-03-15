# app.py
import streamlit as st
import pandas as pd
import sqlite3
import subprocess
import sys
import os
import signal
from datetime import datetime

st.set_page_config(page_title="プライム高配当株スクリーニング", layout="wide")
st.title("高配当株スクリーニング (プライム全業種・全銘柄総当たり版)")

DB_PATH     = "results.db"
WORKER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker.py")

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
                score,
                scanned_at   AS スキャン日時
            FROM scan_results
            ORDER BY score DESC, mcap_oku DESC
        """, conn)
        conn.close()
        return df
    except:
        return pd.DataFrame()

def clear_results():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM scan_results")
        conn.execute("DELETE FROM scan_status")
        conn.commit()
        conn.close()
    except:
        pass

def start_worker():
    subprocess.Popen(
        [sys.executable, WORKER_PATH],
        stdout=open("worker.log", "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True
    )

def stop_worker(pid_str):
    try:
        pid = int(pid_str)
        if sys.platform == "win32":
            subprocess.call(["taskkill", "/F", "/PID", str(pid)])
        else:
            os.kill(pid, signal.SIGTERM)
        return True
    except Exception as e:
        return False

# ステータス取得
status     = get_status()
state      = status.get('state', 'not_started')
is_running = (state == 'running')

# コントロールパネル
st.subheader("スキャン操作")
col1, col2, col3, col4 = st.columns([2, 2, 2, 4])

with col1:
    if st.button(
        "スキャン開始",
        type="primary",
        disabled=is_running,
        help="バックグラウンドでスキャンを開始します。ブラウザを閉じても継続します。"
    ):
        start_worker()
        st.rerun()

with col2:
    if st.button("状態を更新"):
        st.rerun()

with col3:
    # 停止ボタン（実行中のみ有効）
    if st.button(
        "停止・結果削除",
        type="secondary",
        disabled=not is_running,
        help="実行を停止し、途中の結果をすべて削除します。"
    ):
        pid = status.get('pid', '')
        if pid:
            stopped = stop_worker(pid)
            if stopped:
                st.toast("プロセスを停止しました")
            else:
                st.toast("プロセス停止に失敗しました（すでに終了している可能性があります）")
        clear_results()
        st.rerun()

with col4:
    if state == 'running':
        started = status.get('started', '')
        st.info(f"実行中（開始: {started}）")
    elif state == 'done':
        finished = status.get('finished', '')
        st.success(f"完了（{finished}）")
    else:
        st.warning("未実行 → スキャン開始ボタンで実行できます")

# プログレス表示（実行中のみ）
if state == 'running':
    progress = int(status.get('progress', 0))
    current  = status.get('current', '...')
    st.progress(progress / 100, text=f"{progress}%  {current}")
    st.caption("30秒ごとに自動更新されます")
    st.markdown('<meta http-equiv="refresh" content="30">', unsafe_allow_html=True)

# ワーカーログ表示
if os.path.exists("worker.log"):
    with st.expander("実行ログ（worker.log）", expanded=False):
        with open("worker.log", "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        for line in lines[-50:]:
            st.text(line.rstrip())

# 結果表示
st.divider()
df = get_results()

if df.empty:
    st.info("結果がありません。スキャンを実行してください。")
else:
    scanned_at = df['スキャン日時'].iloc[0] if 'スキャン日時' in df.columns else ''
    display_df = df.drop(columns=['score', 'スキャン日時'], errors='ignore')

    st.subheader(f"スクリーニング結果  {len(display_df)} 銘柄  （取得: {scanned_at}）")

    industries = df['業種'].unique().tolist()
    if "商社" in industries:
        industries = ["商社"] + [i for i in industries if i != "商社"]

    tabs = st.tabs(["全件"] + industries)
    with tabs[0]:
        st.dataframe(display_df, use_container_width=True)
    for tab, ind in zip(tabs[1:], industries):
        with tab:
            st.dataframe(
                display_df[display_df['業種'] == ind].reset_index(drop=True),
                use_container_width=True
            )

    # CSVファイル名に実行時刻を含める
    try:
        dt_str = datetime.strptime(scanned_at, '%Y-%m-%d %H:%M:%S').strftime('%Y%m%d_%H%M%S')
    except:
        dt_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_filename = f"Hidividend_{dt_str}.csv"

    csv = display_df.to_csv(index=False).encode('utf-8-sig')
    st.download_button("CSVダウンロード", csv, csv_filename, "text/csv")
