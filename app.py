import streamlit as st
import pandas as pd
import requests
import json
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
                    COALESCE(yutai, '-') as yutai,
                    COALESCE(div_history, '-') as div_history,
                    COALESCE(eps_str, '-') as eps_str,
                    COALESCE(div_trend, '[]') as div_trend
                FROM scan_results
                WHERE scan_id = ?
                ORDER BY yield_pct DESC, score DESC
            """, (scan_id,))
            rows = c.fetchall()
            df = pd.DataFrame(rows, columns=[
                "業種", "コード", "銘柄名", "利回り(%)", "配当性向(%)",
                "自己資本(%)", "時価総額(億)", "判定", "おすすめ度", "備考",
                "score", "スキャン日時", "株主優待", "配当増減歴", "EPS", "_div_trend_json"
            ])
            def _parse_trend(s):
                try:
                    v = json.loads(s) if s and s != "[]" else []
                    return v if isinstance(v, list) and len(v) > 0 else []
                except Exception:
                    return []
            df["配当推移"] = df["_div_trend_json"].apply(_parse_trend)
            df = df.drop(columns=["_div_trend_json"])
            col_order = [
                "業種", "コード", "銘柄名", "利回り(%)", "配当性向(%)",
                "自己資本(%)", "時価総額(億)", "配当増減歴", "配当推移", "EPS",
                "判定", "おすすめ度", "株主優待", "備考",
                "score", "スキャン日時"
            ]
            df = df[col_order]
        else:
            df = pd.DataFrame()
        conn.close()
        return df
    except Exception as e:
        st.error(f"データベースから結果を取得中にエラーが発生しました: {e}")
        return pd.DataFrame()

def get_prev_results(current_scan_id):
    """直前スキャンの結果を取得（変化トラッキング用）"""
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("""
            SELECT DISTINCT scan_id FROM scan_results
            WHERE scan_id != ?
            ORDER BY scan_id DESC LIMIT 1
        """, (current_scan_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return pd.DataFrame()
        prev_id = row[0]
        c.execute("""
            SELECT code,
                   yield_pct, payout_pct, equity_pct,
                   COALESCE(div_history, '-') as div_history,
                   COALESCE(eps_str, '-') as eps_str,
                   COALESCE(yutai, '-') as yutai
            FROM scan_results WHERE scan_id = ?
        """, (prev_id,))
        rows = c.fetchall()
        conn.close()
        return pd.DataFrame(rows, columns=[
            "コード", "利回り(%)", "配当性向(%)", "自己資本(%)",
            "配当増減歴", "EPS", "株主優待"
        ])
    except Exception:
        return pd.DataFrame()

def get_scan_log(scan_id):
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("""
            SELECT industry, code, name, reason, yield_pct
            FROM scan_log
            WHERE scan_id = ?
            ORDER BY industry, code
        """, (scan_id,))
        rows = c.fetchall()
        conn.close()
        return pd.DataFrame(rows, columns=["業種", "コード", "銘柄名", "除外理由", "利回り(%)"])
    except Exception:
        return pd.DataFrame()

import re as _re

def _div_history_score(s):
    if not isinstance(s, str) or s in ("-", ""):
        return 0
    if "👑" in s:
        m = _re.search(r'(\d+)', s)
        return int(m.group(1)) + 100 if m else 100
    m = _re.search(r'(\d+)年連続', s)
    if m:
        return int(m.group(1))
    m = _re.search(r'減配(\d+)回', s)
    if m:
        return -int(m.group(1))
    return 0

def _eps_trailing(s):
    if not isinstance(s, str) or s in ("-", ""):
        return None
    m = _re.search(r'([\d.]+)', s)
    return float(m.group(1)) if m else None

def _yutai_has(s):
    if not isinstance(s, str) or s in ("-", "なし", ""):
        return False
    return True

def compute_changes(current_df, prev_df):
    """code → {col → {good: bool, prev: str, curr: str, increased: bool}}"""
    if prev_df.empty:
        return {}
    prev_map = prev_df.drop_duplicates(subset=["コード"]).set_index("コード").to_dict("index")
    changes = {}
    for _, row in current_df.iterrows():
        code = row["コード"]
        if code not in prev_map:
            continue
        prev = prev_map[code]
        code_changes = {}

        # 利回り(%) — up=good
        try:
            c_v, p_v = float(row["利回り(%)"]), float(prev["利回り(%)"])
            if abs(c_v - p_v) >= 0.05:
                inc = c_v > p_v
                code_changes["利回り(%)"] = {"good": inc, "increased": inc,
                                              "prev": f"{p_v:.2f}%", "curr": f"{c_v:.2f}%"}
        except Exception:
            pass

        # 配当性向(%) — down=good
        try:
            c_v, p_v = float(row["配当性向(%)"]), float(prev["配当性向(%)"])
            if abs(c_v - p_v) >= 1.0:
                inc = c_v > p_v
                code_changes["配当性向(%)"] = {"good": not inc, "increased": inc,
                                                "prev": f"{p_v:.1f}%", "curr": f"{c_v:.1f}%"}
        except Exception:
            pass

        # 自己資本(%) — up=good
        try:
            c_v, p_v = float(row["自己資本(%)"]), float(prev["自己資本(%)"])
            if abs(c_v - p_v) >= 1.0:
                inc = c_v > p_v
                code_changes["自己資本(%)"] = {"good": inc, "increased": inc,
                                                "prev": f"{p_v:.1f}%", "curr": f"{c_v:.1f}%"}
        except Exception:
            pass

        # 配当増減歴 — higher score=good
        try:
            c_s = _div_history_score(row["配当増減歴"])
            p_s = _div_history_score(prev["配当増減歴"])
            if c_s != p_s:
                good = c_s > p_s
                code_changes["配当増減歴"] = {"good": good, "increased": good,
                                              "prev": str(prev["配当増減歴"]), "curr": str(row["配当増減歴"])}
        except Exception:
            pass

        # EPS — trailing up=good
        try:
            c_t = _eps_trailing(row["EPS"])
            p_t = _eps_trailing(prev["EPS"])
            if c_t is not None and p_t is not None and abs(c_t - p_t) >= 1.0:
                good = c_t > p_t
                code_changes["EPS"] = {"good": good, "increased": good,
                                       "prev": str(prev["EPS"]), "curr": str(row["EPS"])}
        except Exception:
            pass

        # 株主優待 — gained=good, lost=bad
        try:
            c_has = _yutai_has(row["株主優待"])
            p_has = _yutai_has(prev["株主優待"])
            if c_has != p_has:
                good = c_has and not p_has
                code_changes["株主優待"] = {"good": good, "increased": good,
                                            "prev": str(prev["株主優待"]), "curr": str(row["株主優待"])}
        except Exception:
            pass

        if code_changes:
            changes[code] = code_changes
    return changes

def apply_changes_to_display(display_df, changes):
    """セル値に前回比を付記したDataFrameを返す"""
    df = display_df.copy()
    for idx, row in df.iterrows():
        code = row["コード"]
        if code not in changes:
            continue
        for col, info in changes[code].items():
            if col not in df.columns:
                continue
            arrow = "↑" if info["increased"] else "↓"
            df.at[idx, col] = f"{row[col]}({arrow}前:{info['prev']})"
    return df

def make_highlighter(filtered_df, changes, is_osusume=False):
    """highlight_best/osusume + 変化セル着色を合成したrow-level styler"""
    src = filtered_df
    def highlight(row):
        code = row["コード"]
        orig = src[src["コード"] == code]
        if orig.empty:
            return [""] * len(row)
        yield_val = float(orig["利回り(%)"].iloc[0])
        score_val = int(orig["score"].iloc[0])

        if yield_val < 3.0:
            return ["background-color: #e0e0e0; color: #888888"] * len(row)

        is_yellow = score_val == 5 and yield_val >= 3.5
        base_bg = "background-color: #fff9c4; font-weight: bold" if is_yellow else ""

        styles = [base_bg] * len(row)

        if code in changes:
            for ci, col in enumerate(row.index):
                if col in changes[code]:
                    info = changes[code][col]
                    text_col = "color: #1565c0" if info["good"] else "color: #c62828"
                    existing = styles[ci]
                    if existing:
                        styles[ci] = existing + "; " + text_col
                    else:
                        styles[ci] = text_col

        return styles
    return highlight

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

_current_sid = selected_scan_id if selected_scan_id else get_latest_scan_id()
df = get_results(_current_sid)
_prev_df = get_prev_results(_current_sid) if _current_sid else pd.DataFrame()

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

    search_query = st.text_input(
        "🔎 キーワード検索",
        placeholder="銘柄名・業種・コード・備考・株主優待など...",
        key="result_search"
    )

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

    def apply_search(df, query):
        """クエリに一致する行のみ返す（大文字小文字無視）"""
        q = query.strip() if query else ""
        if not q:
            return df
        return df[df.apply(
            lambda row: row.astype(str).str.contains(q, case=False, na=False).any(),
            axis=1
        )]

    def with_1idx(df):
        """表示用に1始まりインデックスを付ける"""
        d = df.reset_index(drop=True)
        d.index = d.index + 1
        return d

    def add_star_to_best(df_orig, disp_df):
        res = disp_df.copy()
        for idx, row in res.iterrows():
            code = row["コード"]
            score_val = df_orig.loc[df_orig["コード"] == code, "score"].values
            yield_val = df_orig.loc[df_orig["コード"] == code, "利回り(%)"].values
            note_val  = df_orig.loc[df_orig["コード"] == code, "備考"].values
            has_cut = len(note_val) > 0 and "減配歴" in str(note_val[0])
            if (len(score_val) > 0 and score_val[0] == 5 and
                    len(yield_val) > 0 and yield_val[0] >= 3.5 and not has_cut):
                res.at[idx, "備考"] = "★" + str(row["備考"])
        return res

    starred_df = add_star_to_best(filtered_df, display_df)

    # 変化トラッキング
    _changes = compute_changes(filtered_df, _prev_df)
    starred_with_changes = apply_changes_to_display(starred_df, _changes)
    _highlighter = make_highlighter(filtered_df, _changes)

    disp_id = _current_sid
    st.subheader(f"スクリーニング結果 {len(display_df)} 銘柄  ({disp_id})")
    change_note = "青=前回比良化 / 赤=前回比悪化" if _changes else "前回スキャンなし（変化比較不可）"
    st.caption(f"黄色ハイライト・備考欄★ = 利回り3.5%以上かつおすすめ度★★★★★　|　{change_note}")

    industries = filtered_df["業種"].unique().tolist()
    if "商社" in industries:
        industries = ["商社"] + [i for i in industries if i != "商社"]

    # おすすめタブ用データ（score>=4 かつ 利回り>=3.5%、優待あり優先）
    osusume_src = filtered_df[
        (filtered_df["score"] >= 4) &
        (filtered_df["利回り(%)"] >= 3.5)
    ].copy()

    def _yutai_rank(v):
        if not isinstance(v, str) or v in ("-", ""):
            return 0
        if v == "なし":
            return 1
        return 2

    osusume_src["_yr"] = osusume_src["株主優待"].apply(_yutai_rank)
    osusume_sorted = osusume_src.sort_values(
        ["利回り(%)", "score", "_yr"], ascending=[False, False, False]
    )
    osusume_disp = osusume_sorted.drop(columns=["score", "スキャン日時", "_yr"], errors="ignore")
    osusume_starred = add_star_to_best(osusume_sorted, osusume_disp)
    osusume_with_changes = apply_changes_to_display(osusume_starred, _changes)
    _os_highlighter = make_highlighter(osusume_sorted, _changes, is_osusume=True)

    _col_cfg = {
        "配当推移": st.column_config.LineChartColumn("配当推移(10年)", y_min=0, width="small")
    }

    tabs = st.tabs(["おすすめ", "全件"] + industries)

    with tabs[0]:
        _os = with_1idx(apply_search(osusume_with_changes, search_query))
        if _os.empty:
            st.info("おすすめ条件（利回り3.5%以上・おすすめ度★★★★以上）に該当する銘柄はありません。")
        else:
            st.dataframe(_os.style.apply(_os_highlighter, axis=1),
                         column_config=_col_cfg, use_container_width=True)

    with tabs[1]:
        _all = with_1idx(apply_search(starred_with_changes, search_query))
        st.dataframe(_all.style.apply(_highlighter, axis=1),
                     column_config=_col_cfg, use_container_width=True)

    for tab, ind in zip(tabs[2:], industries):
        with tab:
            ind_src = filtered_df[filtered_df["業種"] == ind]
            ind_disp = with_1idx(apply_search(
                starred_with_changes[starred_with_changes["業種"] == ind], search_query
            ))
            _ind_hl = make_highlighter(ind_src, _changes)
            st.dataframe(ind_disp.style.apply(_ind_hl, axis=1),
                         column_config=_col_cfg, use_container_width=True)

    try:
        dt_str = datetime.strptime(scanned_at, "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d_%H%M%S")
    except:
        dt_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv = starred_with_changes.to_csv(index=False).encode("utf-8-sig")
    st.download_button("CSVダウンロード", csv, f"Hidividend_{dt_str}.csv", "text/csv")

    st.divider()
    with st.expander("🔍 スクリーニングログ（除外銘柄）", expanded=False):
        log_df = get_scan_log(_current_sid)
        if log_df.empty:
            st.info("ログデータがありません（次回スキャン後から記録されます）")
        else:
            log_search = st.text_input(
                "ログ検索",
                placeholder="銘柄名・コード・業種・除外理由など...",
                key="log_search"
            )
            disp_log = log_df
            if log_search.strip():
                disp_log = log_df[log_df.apply(
                    lambda r: r.astype(str).str.contains(log_search.strip(), case=False, na=False).any(),
                    axis=1
                )]
            st.caption(f"除外銘柄: {len(disp_log)} / {len(log_df)} 件")
            st.dataframe(disp_log.reset_index(drop=True), use_container_width=True)

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
