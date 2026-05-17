import streamlit as st
import pandas as pd
import requests
import json
import base64
import os
from datetime import datetime
import time


def _make_div_svg(raw_data):
    """[[year, value], ...] → SVG data URL（年ラベル付きミニ棒グラフ）"""
    if not raw_data or not isinstance(raw_data, list):
        return ""
    data = [d for d in raw_data if isinstance(d, list) and len(d) == 2]
    if not data:
        return ""

    W, H       = 220, 68
    pad_l, pad_r, pad_t, pad_b = 2, 2, 3, 15
    cw = W - pad_l - pad_r
    ch = H - pad_t - pad_b

    values = [float(d[1]) for d in data]
    n      = len(values)
    max_v  = max(values)
    min_v  = min(values)
    if max_v == min_v:
        min_v = max_v * 0.8
    rng   = max_v - min_v if max_v != min_v else 1
    bar_w = cw / n

    parts = []
    for i, (year, val) in enumerate(data):
        x     = pad_l + i * bar_w
        bar_h = max(2.0, (float(val) - min_v) / rng * ch)
        y     = pad_t + ch - bar_h
        fill  = "#1565c0" if i == n - 1 else "#64b5f6"
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" '
            f'width="{max(1.5, bar_w - 1):.1f}" height="{bar_h:.1f}" fill="{fill}"/>'
        )
        # 年ラベル: 10年以下は毎年、11年以上は偶数インデックスのみ
        if n <= 10 or i % 2 == 0:
            lx  = x + bar_w / 2
            yr  = str(year)[-2:]
            parts.append(
                f'<text x="{lx:.1f}" y="{H - 2}" '
                f'font-size="7" text-anchor="middle" fill="#888">{yr}</text>'
            )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}">'
        + "".join(parts)
        + "</svg>"
    )
    b64 = base64.b64encode(svg.encode()).decode()
    return f"data:image/svg+xml;base64,{b64}"

try:
    import libsql_experimental as db_lib
except ImportError:
    import sqlite3 as db_lib

st.set_page_config(page_title="プライム高配当株スクリーニング", layout="wide")

st.markdown("""
<style>
/* ===== 全体リセット ===== */
.block-container { padding-top: 0 !important; padding-bottom: 100px !important; }
header[data-testid="stHeader"] { background: transparent !important; }
#MainMenu, footer { visibility: hidden; }

/* ===== カスタムヘッダー ===== */
.app-header {
    background: linear-gradient(135deg, #0d47a1 0%, #1565c0 100%);
    color: #fff;
    padding: 14px 20px 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: -4rem -4rem 1.5rem -4rem;
}
.app-header .main-title { font-size: 18px; font-weight: 800; letter-spacing: 0.3px; }
.app-header .sub-title  { font-size: 11px; opacity: 0.75; margin-top: 3px; }

/* ===== サマリーカード ===== */
[data-testid="metric-container"] {
    background: #f0f4ff;
    border-radius: 12px;
    padding: 14px 16px !important;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
[data-testid="metric-container"] label { font-size: 11px !important; color: #888 !important; }
[data-testid="metric-container"] [data-testid="stMetricValue"] { font-size: 26px !important; font-weight: 800 !important; color: #1565c0 !important; }
[data-testid="metric-container"] [data-testid="stMetricDelta"] { font-size: 12px !important; }

/* ===== サイドバー ===== */
[data-testid="stSidebar"] > div:first-child { background: #0d1b3e; }
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] .stMarkdown li,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stSlider label { color: rgba(255,255,255,0.85) !important; }
[data-testid="stSidebar"] h3 { color: rgba(255,255,255,0.5) !important; font-size: 10px !important; letter-spacing: 1px; text-transform: uppercase; margin-top: 1rem; }
[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.1) !important; }

/* ===== 実行中バナー ===== */
.running-banner {
    background: linear-gradient(90deg, #fff3e0, #ffe0b2);
    border-left: 4px solid #ff6f00;
    border-radius: 8px;
    padding: 10px 16px;
    margin-bottom: 12px;
    font-weight: 600;
    color: #e65100;
    display: flex;
    align-items: center;
    gap: 10px;
}

/* ===== モバイル: 下部固定ボタンバー ===== */
.mobile-bottom-bar {
    display: none;
    position: fixed;
    bottom: 0; left: 0; right: 0;
    background: #fff;
    border-top: 1px solid #e8eaf0;
    padding: 10px 16px;
    gap: 8px;
    z-index: 999;
}
@media (max-width: 768px) {
    .mobile-bottom-bar { display: flex; }
    .app-header { margin: -1rem -1rem 1rem -1rem; }
    .block-container { padding-bottom: 80px !important; }
}
.mbb-btn {
    flex: 1;
    padding: 10px 6px;
    border-radius: 10px;
    border: none;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
    text-align: center;
}
.mbb-scan    { background: #0d47a1; color: #fff; }
.mbb-refresh { background: #f0f2f6; color: #444; }
.mbb-csv     { background: #f0f2f6; color: #444; }
</style>
""", unsafe_allow_html=True)

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
                """[[year,val],...] または [val,...] どちらにも対応。(values, year_label) を返す"""
                try:
                    data = json.loads(s) if s and s != "[]" else []
                    if not data:
                        return [], ""
                    if isinstance(data[0], list):
                        values = [d[1] for d in data]
                        y0 = str(data[0][0])[-2:]
                        y1 = str(data[-1][0])[-2:]
                        label = f"'{y0}〜'{y1}"
                        return values, label
                    else:
                        return data, ""
                except Exception:
                    return [], ""
            def _extract_raw(s):
                try:
                    data = json.loads(s) if s and s != "[]" else []
                    return data if data and isinstance(data[0], list) else []
                except Exception:
                    return []
            df["配当グラフ"]  = df["_div_trend_json"].apply(_extract_raw).apply(_make_div_svg)
            df = df.drop(columns=["_div_trend_json"])
            col_order = [
                "業種", "コード", "銘柄名", "利回り(%)", "配当性向(%)",
                "自己資本(%)", "時価総額(億)", "配当増減歴", "配当グラフ", "EPS",
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

        # 配当性向(%) — up=good(blue)
        try:
            c_v, p_v = float(row["配当性向(%)"]), float(prev["配当性向(%)"])
            if abs(c_v - p_v) >= 1.0:
                inc = c_v > p_v
                code_changes["配当性向(%)"] = {"good": inc, "increased": inc,
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


        if code_changes:
            changes[code] = code_changes
    return changes

def apply_changes_to_display(display_df, changes):
    """セル値に前回比を付記したDataFrameを返す"""
    df = display_df.copy()
    # 文字列注記を書き込む列はobject型に変換しておく（float列への文字列代入TypeError対策）
    tracked_cols = {"利回り(%)", "配当性向(%)", "自己資本(%)", "配当増減歴", "EPS"}
    for col in tracked_cols:
        if col in df.columns:
            df[col] = df[col].astype(object)
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

# ---------------- サイドバー ----------------
with st.sidebar:
    st.markdown("### アクション")
    if st.button("▶ スキャン開始 (クラウド実行)", type="primary", use_container_width=True):
        with st.spinner("GitHubへスキャン開始の指示を送信中..."):
            success, msg = trigger_github_workflow()
            if success:
                st.session_state["selected_scan_id"] = None
                st.success(msg)
                time.sleep(2)
                st.rerun()
            else:
                st.error(msg)
    if st.button("🔄 最新に更新", use_container_width=True):
        st.session_state["selected_scan_id"] = None
        st.rerun()
    st.link_button("📜 GitHub ライブログ",
                   "https://github.com/yyoshihara10-star/HidividendApp/actions",
                   use_container_width=True)

    st.markdown("---")
    st.markdown("### フィルター")
    min_yield  = st.slider("最低利回り(%)", 3.0, 10.0, 3.0, 0.5)
    max_payout = st.slider("配当性向 上限(%)", 30, 100, 100, 5)
    min_score  = st.selectbox("最低おすすめ度", [1, 2, 3, 4, 5], index=0)
    sort_by    = st.selectbox("ソート順", ["利回り高い順+おすすめ度高い順", "おすすめ度高い順", "配当性向低い順"])

    st.markdown("---")
    with st.expander("スクリーニング条件・情報ソース"):
        st.markdown("""
**対象**: 東証プライム（JPX、毎月1日更新）

**除外条件**
* 利回り3%未満 / 性向30%未満・70%超（回復見込みなし）
* 自己資本40%未満（金融除く）
* 減配2回以上かつ増配傾向なし
* 当年減配確定 / 前年減配かつ今年増配未確認

**おすすめ度（★1〜5）**  基本5点から減点
* 減配2回以上(増配傾向あり) -2 / その他基準外 各-1
* ★5・利回り3.5%以上 → おすすめタブ掲載

**情報ソース**
* 株価・財務: Yahoo Finance
* 株主優待: かぶたん・みんかぶ
""")

    st.markdown("---")
    st.markdown("### スキャン履歴")
    _history_df = get_history()
    _done_history = _history_df[_history_df["status"] == "done"] if not _history_df.empty else pd.DataFrame()
    if not _done_history.empty:
        _hist_options = ["最新"] + [
            f"{r['scan_id']}  ({r['result_count']}銘柄)"
            for _, r in _done_history.iterrows()
        ]
        _hist_ids = [None] + list(_done_history["scan_id"])
        _hist_sel = st.selectbox("表示するスキャン", _hist_options, index=0, label_visibility="collapsed")
        _sel_idx  = _hist_options.index(_hist_sel)
        if _hist_ids[_sel_idx] != st.session_state["selected_scan_id"]:
            st.session_state["selected_scan_id"] = _hist_ids[_sel_idx]
            st.rerun()
        # 過去履歴選択中は削除ボタンを表示
        _viewing_sid = _hist_ids[_sel_idx]
        if _viewing_sid:
            if st.button(f"🗑 削除 ({_viewing_sid})", use_container_width=True):
                delete_scan(_viewing_sid)
                st.session_state["selected_scan_id"] = None
                st.toast(f"{_viewing_sid} を削除しました")
                st.rerun()
    else:
        st.caption("履歴なし")

# ---------------- メインコンテンツ ----------------
selected_scan_id = st.session_state["selected_scan_id"]
status_data      = get_scan_status()
_current_sid     = selected_scan_id if selected_scan_id else get_latest_scan_id()
df               = get_results(_current_sid)
_prev_df         = get_prev_results(_current_sid) if _current_sid else pd.DataFrame()

# カスタムヘッダー
_scan_date = ""
if not df.empty and "スキャン日時" in df.columns:
    try:
        _scan_date = datetime.strptime(df["スキャン日時"].iloc[0], "%Y-%m-%d %H:%M:%S").strftime("%Y年%m月%d日")
    except Exception:
        _scan_date = df["スキャン日時"].iloc[0]
_hist_label = f"履歴表示中: {selected_scan_id}" if selected_scan_id else f"最終更新: {_scan_date}" if _scan_date else "データなし"
st.markdown(f"""
<div class="app-header">
  <div>
    <div class="main-title">高配当株スクリーニング</div>
    <div class="sub-title">{_hist_label}</div>
  </div>
</div>
""", unsafe_allow_html=True)

# 実行中バナー
if status_data.get("state") == "running":
    progress     = int(status_data.get("progress", 0))
    current_task = status_data.get("current", "準備中...")
    st.markdown(f'<div class="running-banner">⏳ スキャン実行中... {progress}% — {current_task}</div>', unsafe_allow_html=True)
    st.progress(progress / 100.0)
    st.caption("30秒ごとに自動更新")
    st.markdown('<meta http-equiv="refresh" content="30">', unsafe_allow_html=True)

if df.empty:
    st.info("まだスキャン結果がありません。サイドバーの「スキャン開始」ボタンを押すか、自動スキャンの完了をお待ちください。")
else:
    scanned_at = df["スキャン日時"].iloc[0] if "スキャン日時" in df.columns else ""

    search_query = st.text_input(
        "",
        placeholder="🔎  銘柄名・コード・業種・備考で検索...",
        key="result_search",
        label_visibility="collapsed"
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
            _NEGATIVE_KEYWORDS = ["減配歴", "成長データ不明", "10年未満", "減配確定",
                                   "売上減", "利益減", "配当性向", "自己資本", "低め)"]
            note_str = str(note_val[0]) if len(note_val) > 0 else ""
            has_negative = any(k in note_str for k in _NEGATIVE_KEYWORDS)
            if (len(score_val) > 0 and score_val[0] == 5 and
                    len(yield_val) > 0 and yield_val[0] >= 3.5 and not has_negative):
                res.at[idx, "備考"] = "★" + str(row["備考"])
        return res

    starred_df = add_star_to_best(filtered_df, display_df)

    # 変化トラッキング
    _changes = compute_changes(filtered_df, _prev_df)
    starred_with_changes = apply_changes_to_display(starred_df, _changes)
    _highlighter = make_highlighter(filtered_df, _changes)

    # おすすめタブ用データを先に計算（サマリーカードに使う）
    def _is_osusume_eligible(note):
        _EXCLUDE = ["配当性向", "低め)"]
        return not any(k in str(note) for k in _EXCLUDE)

    osusume_src = filtered_df[
        (filtered_df["score"] >= 4) &
        (filtered_df["利回り(%)"] >= 3.5) &
        (filtered_df["備考"].apply(_is_osusume_eligible))
    ].copy()

    # サマリーカード
    _max_yield = filtered_df["利回り(%)"].max() if not filtered_df.empty else 0
    _prev_count = len(_prev_df["コード"].unique()) if not _prev_df.empty else None
    _delta_count = len(filtered_df) - _prev_count if _prev_count else None
    _mc1, _mc2, _mc3, _mc4 = st.columns(4)
    with _mc1:
        st.metric("★ おすすめ", f"{len(osusume_src)} 銘柄")
    with _mc2:
        st.metric("全銘柄", f"{len(filtered_df)} 銘柄",
                  delta=f"{_delta_count:+d}" if _delta_count is not None else None)
    with _mc3:
        st.metric("最高利回り", f"{_max_yield:.1f}%")
    with _mc4:
        change_note = "青=良化 / 赤=悪化" if _changes else "前回比較なし"
        st.metric("前回比", change_note)

    st.caption("黄色ハイライト・★ = 利回り3.5%以上かつおすすめ度★★★★★")

    industries = filtered_df["業種"].unique().tolist()
    if "商社" in industries:
        industries = ["商社"] + [i for i in industries if i != "商社"]

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
        "配当グラフ": st.column_config.ImageColumn("配当推移(年付)", width="medium"),
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

