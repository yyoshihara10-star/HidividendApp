import streamlit as st
import pandas as pd
import requests
import os
from datetime import datetime

try:
    import libsql_experimental as db_lib
except ImportError:
    import sqlite3 as db_lib

st.set_page_config(page_title="プライム高配当株スクリーニング", layout="wide")
st.title("高配当株スクリーニング")
st.caption("※スキャンはGitHubのサーバーで安全に実行され、結果がここに表示されます。")

DB_PATH = "results.db"

def get_db_conn():
    db_url = os.environ.get("TURSO_DATABASE_URL")
    auth_token = os.environ.get("TURSO_AUTH_TOKEN")
    try:
        if not db_url and "TURSO_DATABASE_URL" in st.secrets:
            db_url = st.secrets["TURSO_DATABASE_URL"]
        if not auth_token and "TURSO_AUTH_TOKEN" in st.secrets:
            auth_token = st.secrets["TURSO_AUTH_TOKEN"]
    except Exception:
        pass
    
    if db_url and auth_token:
        return db_lib.connect(db_url, auth_token=auth_token)
    else:
        return db_lib.connect(DB_PATH)

def trigger_github_workflow():
    """GitHub Actionsを遠隔で起動するリモコン機能"""
    # ログから推測したあなたのリポジトリ名です（もし違っていたら書き換えてください）
    repo = "yyoshihara10/hidividendapp" 
    url = f"https://api.github.com/repos/{repo}/actions/workflows/scan.yml/dispatches"
    
    try:
        token = st.secrets["GITHUB_TOKEN"]
    except KeyError:
        return False, "StreamlitのSecretsに GITHUB_TOKEN が設定されていません。"

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {"ref": "main"}
    
    res = requests.post(url, headers=headers, json=data)
    if res.status_code == 204:
        return True, "スキャンの指示を送信しました！"
    else:
        return False, f"エラーが発生しました: {res.text}"

def get_history():
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("""
            SELECT scan_id, started_at, finished_at, result_count, status
            FROM scan_history
            ORDER BY started_at DESC
        """)
        rows = c.fetchall()
        df = pd.DataFrame(rows, columns=["scan_id", "started_at", "finished_at", "result_count", "status"])
        conn.close()
        return df
    except:
        return pd.DataFrame()

def get_latest_scan_id():
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("""
            SELECT scan_id FROM scan_results
            WHERE scan_id IS NOT NULL
            ORDER BY scanned_at DESC LIMIT 1
        """)
        row = c.fetchone()
        conn.close()
        return row[0] if row else None
    except:
        return None

def get_results(scan_id=None):
    try:
        conn = get_db_conn()
        if not scan_id:
            scan_id = get_latest_scan_id()
        if scan_id:
            c = conn.cursor()
            c.execute("""
                SELECT
                    industry, code, name, yield_pct, payout_pct,
                    equity_pct, mcap_oku, judge, stars, note,
                    score, scanned_at
                FROM scan_results
                WHERE scan_id = ?
                ORDER BY score DESC, mcap_oku DESC
            """, (scan_id,))
            rows = c.fetchall()
            df = pd.DataFrame(rows, columns=[
                "業種", "コード", "銘柄名", "利回り(%)", "配当性向(%)",
                "自己資本(%)", "時価総額(億)", "判定", "おすすめ度", "備考",
                "score", "スキャン日時"
            ])
        else:
            df = pd.DataFrame()
        conn.close()
        return df
    except:
        return pd.DataFrame()

def delete_scan(scan_id):
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("DELETE FROM scan_results WHERE scan_id = ?", (scan_id,))
        c.execute("DELETE FROM scan_history WHERE scan_id = ?", (scan_id,))
        conn.commit()
        conn.close()
    except:
        pass

# セッション初期化
if "selected_scan_id" not in st.session_state:
    st.session_state["selected_scan_id"] = None

# ---------------- UI部分 ----------------

st.divider()

col1, col2 = st.columns([1, 1])
with col1:
    if st.button("▶️ スキャンを手動で開始 (クラウド実行)", type="primary", use_container_width=True):
        with st.spinner("GitHubへスキャン開始の指示を送信中..."):
            success, msg = trigger_github_workflow()
            if success:
                st.success(f"{msg} (裏側でスキャンが始まりました。約10〜20分後に右の更新ボタンを押して確認してください)")
            else:
                st.error(msg)
with col2:
    if st.button("🔄 データを最新に更新", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

st.divider()

selected_scan_id = st.session_state["selected_scan_id"]
df = get_results(selected_scan_id)

if df.empty:
    st.info("まだスキャン結果がありません。上の「スキャンを開始」ボタンを押すか、自動スキャンの完了をお待ちください。")
else:
    scanned_at = df["スキャン日時"].iloc[0] if "スキャン日時" in df.columns else ""
    display_df = df.drop(columns=["score", "スキャン日時"], errors="ignore")
    best_per_industry = df.groupby("業種")["score"].max().to_dict()

    def add_star_to_best(df_orig, disp_df, best_dict):
        res = disp_df.copy()
        for idx, row in res.iterrows():
            industry  = row["業種"]
            name      = row["銘柄名"]
            best      = best_dict.get(industry, -1)
            score_val = df_orig.loc[(df_orig["業種"] == industry) & (df_orig["銘柄名"] == name), "score"].values
            if len(score_val) > 0 and score_val[0] == best:
                res.at[idx, "備考"] = "★" + str(row["備考"])
        return res

    starred_df = add_star_to_best(df, display_df, best_per_industry)

    def highlight_best(row):
        score_val = df.loc[(df["業種"] == row["業種"]) & (df["銘柄名"] == row["銘柄名"]), "score"].values
        best = best_per_industry.get(row["業種"], -1)
        if len(score_val) > 0 and score_val[0] == best:
            return ["background-color: #fff9c4; font-weight: bold"] * len(row)
        return [""] * len(row)

    disp_id = selected_scan_id if selected_scan_id else get_latest_scan_id()
    st.subheader(f"スクリーニング結果 {len(display_df)} 銘柄  ({disp_id})")
    st.caption("黄色ハイライト・備考欄★ = 各業種トップ推奨")

    industries = df["業種"].unique().tolist()
    if "商社" in industries:
        industries = ["商社"] + [i for i in industries if i != "商社"]

    tabs = st.tabs(["全件"] + industries)

    with tabs[0]:
        st.dataframe(starred_df.style.apply(highlight_best, axis=1), use_container_width=True)

    for tab, ind in zip(tabs[1:], industries):
        with tab:
            ind_df = starred_df[starred_df["業種"] == ind].reset_index(drop=True)
            best   = best_per_industry.get(ind, -1)
            def highlight_ind(row, b=best, i=ind):
                score_val = df.loc[(df["業種"] == i) & (df["銘柄名"] == row["銘柄名"]), "score"].values
                if len(score_val) > 0 and score_val[0] == b:
                    return ["background-color: #fff9c4; font-weight: bold"] * len(row)
                return [""] * len(row)
            st.dataframe(ind_df.style.apply(highlight_ind, axis=1), use_container_width=True)

    try:
        dt_str = datetime.strptime(scanned_at, "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d_%H%M%S")
    except:
        dt_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv = starred_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("CSVダウンロード", csv, f"Hidividend_{dt_str}.csv", "text/csv")

st.divider()
st.subheader("過去の履歴")
history_df = get_history()
if not history_df.empty:
    for _, r in history_df[history_df["status"] == "done"].iterrows():
        sid = r["scan_id"]
        info_str = str(r["started_at"]) + f"  {r['result_count']}銘柄"
        
        c1, c2 = st.columns([10, 1])
        with c1:
            is_selected = (st.session_state["selected_scan_id"] == sid)
            label = f"▶ {sid} ({info_str})" if is_selected else f"{sid} ({info_str})"
            if st.button(label, key=f"btn_{sid}", use_container_width=True):
                st.session_state["selected_scan_id"] = sid
                st.rerun()
        with c2:
            if st.button("🗑", key=f"del_{sid}"):
                delete_scan(sid)
                if st.session_state["selected_scan_id"] == sid:
                    st.session_state["selected_scan_id"] = None
                st.toast(f"{sid} を削除しました")
                st.rerun()
else:
    st.info("保存された履歴はありません")
