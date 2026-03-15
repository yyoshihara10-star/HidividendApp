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

def get_history():
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql("""
            SELECT scan_id, started_at, finished_at, result_count, status
            FROM scan_history
            ORDER BY started_at DESC
        """, conn)
        conn.close()
        return df
    except:
        return pd.DataFrame()

def get_past_scan_ids():
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT DISTINCT scan_id, MIN(scanned_at) as started_at, COUNT(*) as cnt
            FROM scan_results
            WHERE scan_id IS NOT NULL
            GROUP BY scan_id
            ORDER BY started_at DESC
        """).fetchall()
        conn.close()
        return rows
    except:
        return []

def get_results(scan_id=None):
    try:
        conn = sqlite3.connect(DB_PATH)
        if scan_id:
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
                WHERE scan_id = ?
                ORDER BY score DESC, mcap_oku DESC
            """, conn, params=(scan_id,))
        else:
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

def delete_scan(scan_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM scan_results WHERE scan_id = ?", (scan_id,))
        conn.execute("DELETE FROM scan_history WHERE scan_id = ?", (scan_id,))
        conn.commit()
        conn.close()
    except:
        pass

def clear_status():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM scan_status")
        conn.commit()
        conn.close()
    except:
        pass

def start_worker():
    try:
        if not os.path.exists(WORKER_PATH):
            return False, "worker.py が見つかりません: " + WORKER_PATH
        os.makedirs("logs", exist_ok=True)
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

def read_log(tail=None):
    if not os.path.exists("worker.log"):
        return ""
    with open("worker.log", "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    if tail:
        lines = lines[-tail:]
    return "".join(lines)

# セッション初期化
if "selected_scan_id" not in st.session_state:
    st.session_state["selected_scan_id"] = None
if "log_show_all" not in st.session_state:
    st.session_state["log_show_all"] = False

# 起動時に不整合状態を自動修正
_status = get_status()
_state  = _status.get("state", "not_started")
if _state == "done" and get_results(_status.get("scan_id")).empty:
    clear_status()

status = get_status()
state  = status.get("state", "not_started")

# 状態バナー
if state == "running":
    progress = int(status.get("progress", 0))
    current  = status.get("current", "...")
    started  = status.get("started", "")
    st.success("### スキャン実行中")
    st.progress(progress / 100, text=str(progress) + "%  " + current)
    st.caption("開始時刻: " + started)
elif state == "done":
    finished = status.get("finished", "")
    current  = status.get("current", "")
    st.success("### " + current + "  (" + finished + ")")
else:
    st.info("### 未実行  スキャン開始ボタンで実行してください")

st.divider()

# 操作ボタン
col1, col2, col3 = st.columns([2, 2, 2])

with col1:
    if st.button("スキャン開始", type="primary", disabled=(state == "running")):
        ok, result = start_worker()
        if ok:
            st.session_state["selected_scan_id"] = None
            st.session_state["log_show_all"] = False
            st.toast("スキャンを開始しました (PID: " + str(result) + ")")
            time.sleep(2)
            st.rerun()
        else:
            st.error("起動失敗: " + str(result))

with col2:
    if st.button("状態を更新"):
        st.cache_data.clear()
        st.rerun()

with col3:
    if st.button("停止・結果削除", type="secondary", disabled=(state != "running")):
        pid     = status.get("pid", "")
        scan_id = status.get("scan_id", "")
        if pid:
            stop_worker(pid)
        if scan_id:
            delete_scan(scan_id)
        clear_status()
        st.session_state["selected_scan_id"] = None
        st.toast("停止しました")
        time.sleep(1)
        st.rerun()

# 実行ログ
if os.path.exists("worker.log"):
    with st.expander("実行ログ", expanded=(state == "running")):
        lcol1, lcol2 = st.columns([8, 2])
        with lcol2:
            if st.button("全件表示" if not st.session_state["log_show_all"] else "最新100行に戻す"):
                st.session_state["log_show_all"] = not st.session_state["log_show_all"]
                st.rerun()
        log_content = read_log(tail=None if st.session_state["log_show_all"] else 100)
        st.code(log_content, language="text")

st.divider()

# 履歴リスト構築
current_scan_id = status.get("scan_id", None)
history_df      = get_history()
past_scan_ids   = get_past_scan_ids()

done_ids = {}
if not history_df.empty:
    for _, r in history_df[history_df["status"] == "done"].iterrows():
        if r["scan_id"] != current_scan_id:
            done_ids[r["scan_id"]] = r["started_at"] + "  " + str(r["result_count"]) + "銘柄"

existing = set(done_ids.keys())
for row in past_scan_ids:
    sid, started_at, cnt = row[0], row[1], row[2]
    if sid not in existing and sid != current_scan_id:
        done_ids[sid] = str(started_at) + "  " + str(cnt) + "銘柄"

# 履歴選択UI
st.subheader("参照する結果を選択")

if state == "running" and current_scan_id:
    c1, c2 = st.columns([10, 1])
    with c1:
        is_selected = (st.session_state["selected_scan_id"] == current_scan_id)
        label = "▶ 現在のスキャン（実行中）" if is_selected else "現在のスキャン（実行中）"
        if st.button(label, key="btn_current", use_container_width=True):
            st.session_state["selected_scan_id"] = current_scan_id
            st.rerun()

for sid, info_str in done_ids.items():
    c1, c2 = st.columns([10, 1])
    with c1:
        is_selected = (st.session_state["selected_scan_id"] == sid)
        label = "▶ " + sid + "  (" + info_str + ")" if is_selected else sid + "  (" + info_str + ")"
        if st.button(label, key="btn_" + sid, use_container_width=True):
            st.session_state["selected_scan_id"] = sid
            st.rerun()
    with c2:
        if st.button("🗑", key="del_" + sid):
            delete_scan(sid)
            if st.session_state["selected_scan_id"] == sid:
                st.session_state["selected_scan_id"] = None
            st.toast(sid + " を削除しました")
            st.rerun()

# 実行中で未選択の場合は現在のスキャンを自動選択
if state == "running" and st.session_state["selected_scan_id"] is None and current_scan_id:
    st.session_state["selected_scan_id"] = current_scan_id

if state == "running":
    st.markdown('<meta http-equiv="refresh" content="30">', unsafe_allow_html=True)

selected_scan_id = st.session_state["selected_scan_id"]

st.divider()

df = get_results(selected_scan_id)

if df.empty:
    if state == "running":
        st.info("スキャン実行中です。結果が取得され次第ここに表示されます。")
    else:
        st.info("結果がありません")
else:
    scanned_at = df["スキャン日時"].iloc[0] if "スキャン日時" in df.columns else ""
    display_df = df.drop(columns=["score", "スキャン日時"], errors="ignore")

    best_per_industry = df.groupby("業種")["score"].max().to_dict()

    def highlight_best(row):
        score_val = df.loc[
            (df["業種"] == row["業種"]) &
            (df["銘柄名"] == row["銘柄名"]),
            "score"
        ].values
        best = best_per_industry.get(row["業種"], -1)
        if len(score_val) > 0 and score_val[0] == best:
            return ["background-color: #fff9c4; font-weight: bold"] * len(row)
        return [""] * len(row)

    label = "  (" + (selected_scan_id if selected_scan_id else "最新") + ")"
    st.subheader("スクリーニング結果  " + str(len(display_df)) + " 銘柄" + label)
    st.caption("黄色ハイライト = 各業種トップ推奨（同率の場合は複数）")

    industries = df["業種"].unique().tolist()
    if "商社" in industries:
        industries = ["商社"] + [i for i in industries if i != "商社"]

    tabs = st.tabs(["全件"] + industries)

    with tabs[0]:
        st.dataframe(
            display_df.style.apply(highlight_best, axis=1),
            use_container_width=True
        )

    for tab, ind in zip(tabs[1:], industries):
        with tab:
            ind_df = display_df[display_df["業種"] == ind].reset_index(drop=True)
            best   = best_per_industry.get(ind, -1)
            def highlight_ind(row, b=best, i=ind):
                score_val = df.loc[
                    (df["業種"] == i) &
                    (df["銘柄名"] == row["銘柄名"]),
                    "score"
                ].values
                if len(score_val) > 0 and score_val[0] == b:
                    return ["background-color: #fff9c4; font-weight: bold"] * len(row)
                return [""] * len(row)
            st.dataframe(
                ind_df.style.apply(highlight_ind, axis=1),
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
