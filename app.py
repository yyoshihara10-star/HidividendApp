import streamlit as st
import pandas as pd
import requests
import os
from datetime import datetime
import time

try:
    import libsql_experimental as db_lib
except ImportError:
    import sqlite3 as db_lib

st.set_page_config(page_title="プライム高配当株スクリーニング", layout="wide")
st.title("高配当株スクリーニング")

st.markdown("""
**【現在のスクリーニング条件】**
* **市場**: 東証プライム市場
* **配当利回り**: 3.0% 以上
* **配当履歴**: 過去10年で減配1回以内（2回以上は増配傾向のみ許容）
* **配当性向**: 30% 〜 70%（70%超は業績回復見込みのみ許容）
* **自己資本比率**: 40% 以上（金融系を除く）
* **業績**: 直近の売上・利益がマイナス成長でないこと
""")
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
    repo = "yyoshihara10-star/HidividendApp" 
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

def get_scan_status():
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT key, value FROM scan_status")
        rows = c.fetchall()
        conn.close()
        return {k: v for k, v in rows}
    except Exception as e:
        st.error(f"ステータス取得エラー: {e}")
        return {}

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
    except Exception as e:
        st.error(f"履歴取得エラー: {e}")
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
                    score, scanned_at,
                    COALESCE(yutai, '-') as yutai
                FROM scan_results
                WHERE scan_id = ?
                ORDER BY yield_pct DESC, score DESC
            """, (scan_id,))
            rows = c.fetchall()
            df = pd.DataFrame(rows, columns=[
                "業種", "コード", "銘柄名", "利回り(%)", "配当性向(%)",
                "自己資本(%)", "時価総額(億)", "判定", "おすすめ度", "備考",
                "score", "スキャン日時", "株主優待"
            ])
        else:
            df = pd.DataFrame()
        conn.close()
        return df
    # ★ エラーを握りつぶさず、画面に出すようにしました！
    except Exception as e:
        st.error(f"データベースから結果を取得中にエラーが発生しました: {e}")
        return pd.DataFrame()

def delete_scan(scan_id):
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("DELETE FROM scan_results WHERE scan_id = ?", (scan_id,))
        c.execute("DELETE FROM scan_history WHERE scan_id = ?", (scan_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"削除エラー: {e}")

# セッション初期化
if "selected_scan_id" not in st.session_state:
    st.session_state["selected_scan_id"] = None

# ---------------- UI部分 ----------------

st.divider()

col1, col2, col3 = st.columns([4, 3, 3])

with col1:
    if st.button("▶️ スキャンを手動で開始 (クラウド実行)", type="primary", use_container_width=True):
        with st.spinner("GitHubへスキャン開始の指示を送信中..."):
            success, msg = trigger_github_workflow()
            if success:
                st.session_state["selected_scan_id"] = None # ★スキャン開始時は自動で「現在」に戻す
                st.success(msg)
                time.sleep(2)
                st.rerun()
            else:
                st.error(msg)

with col2:
    # ★このボタンを押すと、過去の選択が解除されて「現在（最新）」に戻ります！
    if st.button("🔄 最新の状態に戻す / 更新", use_container_width=True):
        st.session_state["selected_scan_id"] = None 
        st.rerun()

with col3:
    st.link_button(
        "📜 ライブログ (GitHub) を見る", 
        "https://github.com/yyoshihara10-star/HidividendApp/actions", 
        use_container_width=True
    )

selected_scan_id = st.session_state["selected_scan_id"]

# ★過去の履歴を見ている時は、わかりやすく警告を出すようにしました
if selected_scan_id:
    st.warning(f"🕒 現在、過去の履歴 ({selected_scan_id}) を表示しています。「最新の状態に戻す / 更新」ボタンで現在の状況に戻れます。")

# 裏側の進捗状況をチェックして表示する
status_data = get_scan_status()
if status_data.get("state") == "running":
    progress = int(status_data.get("progress", 0))
    current_task = status_data.get("current", "準備中...")
    st.info("⏳ **現在クラウドでスキャンを実行中です**")
    st.progress(progress / 100.0, text=f"進捗: {progress}% - 現在の処理: {current_task}")
    st.caption("30秒ごとに自動更新されます")
    time.sleep(0)
    st.markdown('<meta http-equiv="refresh" content="30">', unsafe_allow_html=True)

st.divider()

df = get_results(selected_scan_id)

if df.empty:
    st.info("まだスキャン結果がありません。上の「スキャンを開始」ボタンを押すか、自動スキャンの完了をお待ちください。")
else:
    scanned_at = df["スキャン日時"].iloc[0] if "スキャン日時" in df.columns else ""

    with st.expander("🔍 フィルター・ソート", expanded=False):
        f_col1, f_col2, f_col3, f_col4 = st.columns(4)
        with f_col1:
            min_yield = st.slider("最低利回り(%)", 3.0, 10.0, 3.0, 0.5)
        with f_col2:
            max_payout = st.slider("最大配当性向(%)", 30, 100, 100, 5)
        with f_col3:
            min_score = st.selectbox("最低おすすめ度", [1, 2, 3, 4, 5], index=0)
        with f_col4:
            sort_by = st.selectbox("ソート", ["利回り高い順+おすすめ度高い順", "おすすめ度高い順", "配当性向低い順"])

    filtered_df = df[
        (df["利回り(%)"] >= min_yield) &
        (df["配当性向(%)"] <= max_payout) &
        (df["score"] >= min_score)
    ].copy()

    if sort_by == "おすすめ度高い順":
        filtered_df = filtered_df.sort_values("score", ascending=False)
    elif sort_by == "配当性向低い順":
        filtered_df = filtered_df.sort_values("配当性向(%)", ascending=True)
    else:
        filtered_df = filtered_df.sort_values(["利回り(%)", "score"], ascending=[False, False])

    display_df = filtered_df.drop(columns=["score", "スキャン日時"], errors="ignore")

    def add_star_to_best(df_orig, disp_df):
        res = disp_df.copy()
        for idx, row in res.iterrows():
            code = row["コード"]
            score_val = df_orig.loc[df_orig["コード"] == code, "score"].values
            yield_val = df_orig.loc[df_orig["コード"] == code, "利回り(%)"].values
            if len(score_val) > 0 and score_val[0] == 5 and len(yield_val) > 0 and yield_val[0] >= 3.5:
                res.at[idx, "備考"] = "★" + str(row["備考"])
        return res

    starred_df = add_star_to_best(filtered_df, display_df)

    def highlight_best(row):
        if row["利回り(%)"] < 3.0:
            return ["background-color: #e0e0e0; color: #888888"] * len(row)
        score_val = filtered_df.loc[filtered_df["コード"] == row["コード"], "score"].values
        if len(score_val) > 0 and score_val[0] == 5 and row["利回り(%)"] >= 3.5:
            return ["background-color: #fff9c4; font-weight: bold"] * len(row)
        return [""] * len(row)

    disp_id = selected_scan_id if selected_scan_id else get_latest_scan_id()
    st.subheader(f"スクリーニング結果 {len(display_df)} 銘柄  ({disp_id})")
    st.caption("黄色ハイライト・備考欄★ = 利回り3.5%以上かつおすすめ度★★★★★")

    industries = filtered_df["業種"].unique().tolist()
    if "商社" in industries:
        industries = ["商社"] + [i for i in industries if i != "商社"]

    tabs = st.tabs(["全件"] + industries)

    with tabs[0]:
        st.dataframe(starred_df.style.apply(highlight_best, axis=1), use_container_width=True)

    for tab, ind in zip(tabs[1:], industries):
        with tab:
            ind_df = starred_df[starred_df["業種"] == ind].reset_index(drop=True)
            def highlight_ind(row, i=ind):
                if row["利回り(%)"] < 3.0:
                    return ["background-color: #e0e0e0; color: #888888"] * len(row)
                score_val = filtered_df.loc[
                    (filtered_df["業種"] == i) & (filtered_df["コード"] == row["コード"]), "score"
                ].values
                if len(score_val) > 0 and score_val[0] == 5 and row["利回り(%)"] >= 3.5:
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
