import streamlit as st
import pandas as pd
import sqlite3
import subprocess
import sys
import os
import signal
import time
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

def clear_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM scan_results")
        conn.execute("DELETE FROM scan_status")
        conn.commit()
        conn.close()
    except:
        pass

def start_worker():
    try:
        if not os.path.exists(WORKER_PATH):
            return False, "worker.py が見つかりません: " + WORKER_PATH
        log = open("worker.log", "w", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, WORKER_PATH],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True
        )
        return True, proc.pid
    except Exception as e:
        return False, str(e)

def stop_worker(pid_str):
    try:
        pid = int(pid_str)
        if sys.platform == "win32":
            subprocess.call(["taskkill", "/F", "/PID", str(pid)])
        else:
            os.kill(pid, signal.SIGTERM)
        return True
    except:
        return False

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 起動時に不整合状態を自動修正
# done なのに結果0件 → リセット
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_status = get_status()
_state  = _status.get("state", "not_started")
if _state == "done" and get_results().empty:
    clear_db()

# ステータス再取得（修正後）
status = get_status()
state  = status.get("state", "not_started")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 状態バナー
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if state == "running":
    progress = int(status.get("progress", 0))
    current  = status.get("current", "...")
    started  = status.get("started", "")
    st.success("### スキャン実行中")
    st.progress(progress / 100, text=str(progress) + "%  " + current)
    st.caption("開始時刻: " + started + "  |  30秒ごとに自動更新")
    st.markdown('<meta http-equiv="refresh" content="30">', unsafe_allow_html=True)

elif state == "done":
    finished = status.get("finished", "")
    current  = status.get("current", "")
    st.success("### スキャン完了  " + current + "  (" + finished + ")")

else:
    st.info("### 未実行  スキャン開始ボタンで実行してください")

st.divider()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 操作ボタン
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
col1, col2, col3 = st.columns([2, 2, 2])

with col1:
    if st.button("スキャン開始", type="primary", disabled=(state == "running")):
        clear_db()
        ok, result = start_worker()
        if ok:
            st.toast("スキャンを開始しました (PID: " + str(result) + ")")
            time.sleep(2)
            st.rerun()
        else:
            st.error("起動失敗: " + str(result))

with col2:
    if st.button("状態を更新"):
        st.rerun()

with col3:
    if st.button("停止・結果削除", type="secondary", disabled=(state != "running")):
        pid = status.get("pid", "")
        if pid:
            stop_worker(pid)
        clear_db()
        st.toast("停止しました")
        st.rerun()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 実行ログ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if os.path.exists("worker.log"):
    with st.expander("実行ログ", expanded=(state == "running")):
        with open("worker.log", "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        st.code("".join(lines[-50:]), language="text")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 結果表示
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.divider()
df = get_results()

if df.empty:
    if state != "running":
        st.info("結果がありません")
else:
    scanned_at = df["スキャン日時"].iloc[0] if "スキャン日時" in df.columns else ""
    display_df = df.drop(columns=["score", "スキャン日時"], errors="ignore")
    st.subheader("スクリーニング結果  " + str(len(display_df)) + " 銘柄")

    industries = df["業種"].unique().tolist()
    if "商社" in industries:
        industries = ["商社"] + [i for i in industries if i != "商社"]

    tabs = st.tabs(["全件"] + industries)
    with tabs[0]:
        st.dataframe(display_df, use_container_width=True)
    for tab, ind in zip(tabs[1:], industries):
        with tab:
            st.dataframe(
                display_df[display_df["業種"] == ind].reset_index(drop=True),
                use_container_width=True
            )

    try:
        dt_str = datetime.strptime(scanned_at, "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d_%H%M%S")
    except:
        dt_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv = display_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "CSVダウンロード",
        csv,
        "Hidividend_" + dt_str + ".csv",
        "text/csv"
    )
