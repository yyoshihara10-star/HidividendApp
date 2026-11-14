import pandas as pd
import yfinance as yf
from curl_cffi import requests
from bs4 import BeautifulSoup
import re
import json
import time
import random
import os
import sys
from datetime import datetime, timezone, timedelta

try:
    import libsql_experimental as db_lib
except ImportError:
    import sqlite3 as db_lib

sys.stdout.reconfigure(line_buffering=True)

SOGO_SHOSHA_CODES = {8058, 8031, 8001, 8053, 8002, 8015, 2768}
MIN_YIELD  = 3.0
MIN_MCAP_OKU = 300  # 時価総額の足切り（億円）。長期高配当投資の対象規模の下限（2026-11-08追加）
INFO_WAIT  = 1.0
MAX_RETRY  = 4  # リトライ回数を少し増やして粘り強くしました
DB_PATH    = "results.db"

JST = timezone(timedelta(hours=9))

def get_robust_session():
    browsers = ["chrome", "safari", "edge"]
    session = requests.Session(impersonate=random.choice(browsers))
    return session

def now_jst():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

def now_jst_id():
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")

def get_db_conn():
    db_url = os.environ.get("TURSO_DATABASE_URL")
    auth_token = os.environ.get("TURSO_AUTH_TOKEN")
    
    if db_url and auth_token:
        return db_lib.connect(db_url, auth_token=auth_token)
    else:
        return db_lib.connect(DB_PATH)

def init_db():
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT,
            scanned_at TEXT,
            industry TEXT,
            code INTEGER,
            name TEXT,
            yield_pct REAL,
            payout_pct REAL,
            equity_pct REAL,
            mcap_oku INTEGER,
            judge TEXT,
            stars TEXT,
            note TEXT,
            score INTEGER,
            score_sustain REAL,
            score_finance REAL,
            score_growth REAL,
            score_return REAL
        )
    """)
    existing = [row[1] for row in c.execute("PRAGMA table_info(scan_results)").fetchall()]
    if "scan_id" not in existing:
        c.execute("ALTER TABLE scan_results ADD COLUMN scan_id TEXT")
    try:
        c.execute("ALTER TABLE scan_results ADD COLUMN yutai TEXT")
        conn.commit()
    except:
        pass
    try:
        c.execute("ALTER TABLE scan_results ADD COLUMN div_history TEXT")
        conn.commit()
    except:
        pass
    try:
        c.execute("ALTER TABLE scan_results ADD COLUMN eps_str TEXT")
        conn.commit()
    except:
        pass
    try:
        c.execute("ALTER TABLE scan_results ADD COLUMN div_trend TEXT")
        conn.commit()
    except:
        pass
    for _axis_col in ("score_sustain", "score_finance", "score_growth", "score_return"):
        try:
            c.execute("ALTER TABLE scan_results ADD COLUMN " + _axis_col + " REAL")
            conn.commit()
        except:
            pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS scan_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT,
            scanned_at TEXT,
            industry TEXT,
            code INTEGER,
            name TEXT,
            reason TEXT,
            yield_pct REAL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS scan_status (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS scan_history (
            scan_id TEXT PRIMARY KEY,
            started_at TEXT,
            finished_at TEXT,
            result_count INTEGER,
            status TEXT
        )
    """)
    conn.commit()
    conn.close()

def set_status(key, value):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO scan_status VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def save_results_batch(rows, scan_id):
    """業種単位で追記保存（全削除→再INSERT方式をやめる）"""
    if not rows:
        return
    conn = get_db_conn()
    c = conn.cursor()
    c.executemany("""
        INSERT INTO scan_results
        (scan_id, scanned_at, industry, code, name, yield_pct, payout_pct,
         equity_pct, mcap_oku, judge, stars, note, score, yutai, div_history, eps_str, div_trend,
         score_sustain, score_finance, score_growth, score_return)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()
    conn.close()

def save_log_batch(rows, scan_id):
    if not rows:
        return
    conn = get_db_conn()
    c = conn.cursor()
    c.executemany("""
        INSERT INTO scan_log (scan_id, scanned_at, industry, code, name, reason, yield_pct)
        VALUES (?,?,?,?,?,?,?)
    """, rows)
    conn.commit()
    conn.close()

def clear_scan_results(scan_id):
    """スキャン開始時に1回だけ呼ぶ"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("DELETE FROM scan_results WHERE scan_id = ?", (scan_id,))
    conn.commit()
    conn.close()

def update_history(scan_id, started_at, finished_at, count, status):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO scan_history
        (scan_id, started_at, finished_at, result_count, status)
        VALUES (?,?,?,?,?)
    """, (scan_id, started_at, finished_at, count, status))
    conn.commit()
    conn.close()

def fetch_jpx_prime():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"
    df = pd.read_excel(url, header=0)
    col_map = {}
    for col in df.columns:
        c = str(col).strip()
        if c == "コード":
            col_map["code"] = col
        elif c == "銘柄名":
            col_map["name"] = col
        elif "市場" in c or "商品区分" in c:
            col_map["market"] = col
        elif "33業種区分" in c:
            col_map["industry"] = col
        elif "規模区分" in c:
            col_map["size"] = col
        elif "規模コード" in c:
            col_map["size_code"] = col
    return df, col_map

def get_sector_targets(sector_df, col_map):
    # 2026-11-08: 業種ごとの上位30銘柄キャップを撤廃（規模順ソートは維持し全銘柄を解析する）
    df = sector_df.copy()

    if "size_code" in col_map and col_map["size_code"] in df.columns:
        df["_sort"] = pd.to_numeric(df[col_map["size_code"]], errors="coerce").fillna(99)
        df = df.sort_values("_sort")
        return df

    if "size" in col_map and col_map["size"] in df.columns:
        def size_rank(v):
            v = str(v).strip()
            if "大型" in v or "large" in v.lower():
                return 1
            elif "中型" in v or "mid" in v.lower():
                return 2
            elif "小型" in v or "small" in v.lower():
                return 3
            else:
                return 99
        df["_sort"] = df[col_map["size"]].apply(size_rank)
        df = df.sort_values("_sort")
        return df

    return df

def fiscal_year_series(divs):
    """配当Seriesを決算期（期別）単位に集計し (年次Series, 期末月) を返す。

    暦年集計だと中間配当の導入・廃止や支払時期の変更で金額が期をまたいで移動し、
    増配が減配に見える誤判定が起きる（例: 全国保証7164、2026-11-09に実証。
    期別では106→120円の増配なのに暦年合計では151→125円の減配に見えた）。
    期末月は過去の支払合計が最大の月で推定する（期末配当が最大であることが多い）。
    Yahooの配当額は株式分割調整済みであることを7164・8111で確認済み。
    """
    months_total = divs.groupby(divs.index.month).sum()
    if len(months_total) == 0:
        return divs.resample("YE").sum(), 12
    end_month = int(months_total.idxmax())
    fy_year = [d.year if d.month <= end_month else d.year + 1 for d in divs.index]
    s = pd.Series(divs.values, index=fy_year)
    annual = s.groupby(level=0).sum().sort_index()
    idx = pd.to_datetime([str(y) + "-" + str(end_month) + "-01" for y in annual.index]) + pd.offsets.MonthEnd(0)
    annual.index = idx
    return annual, end_month


def check_dividend_history(ticker):
    """配当履歴を分析し dict で返す（全経路で同じキーを持つ）"""
    result = {
        "cut_count": 0,
        "years_checked": 0,
        "is_increasing": False,
        "detail": "配当履歴なし",
        "annual_list": [],
        "consecutive_increase": 0,
        "consecutive_noncut": 0,
        "last_year_cut": False,
        "cut_years": [],
        "trend_ratio": None,
        "special_suspect_years": [],
    }
    try:
        divs = ticker.dividends
        if divs is None or len(divs) == 0:
            return result

        divs.index = divs.index.tz_localize(None) if divs.index.tzinfo else divs.index
        cutoff = pd.Timestamp.now() - pd.DateOffset(years=10)
        divs   = divs[divs.index >= cutoff]

        if len(divs) == 0:
            return result

        annual, _fy_end_month = fiscal_year_series(divs)
        annual = annual[annual > 0]
        # 進行中の期（期末日が未来）は中間配当しか入っておらず不完全なため除外
        annual = annual[annual.index <= pd.Timestamp.now()]

        def _annual_data(a):
            return [[int(a.index[i].year), round(float(a.iloc[i]), 1)] for i in range(len(a))]

        result["annual_list"]   = _annual_data(annual)
        result["years_checked"] = len(annual)

        if len(annual) < 2:
            result["detail"] = "配当履歴" + str(len(annual)) + "年分"
            if len(annual) == 1:
                result["consecutive_noncut"] = 1
            return result

        # 記念・特別配当の疑い（2026-11-14追加）: 前年比+15%以上の単年ジャンプで、
        # 前年自体は穏やか（階段型の増配ではない）かつ翌年に5%以上戻した年は、
        # 一時的な増配（記念配当等）とみなし、その年の配当を前年水準に置き換えた
        # 調整後系列で減配・傾向を判定する。記念分の剥落を減配と数えないため。
        # 例: 三洋貿易3176（80周年記念配当の翌期が普通配当のみで名目減になった）
        annual_adj = annual.copy()
        spike_years = []
        for i in range(2, len(annual) - 1):
            prev_v = annual.iloc[i - 1]
            curr_v = annual.iloc[i]
            next_v = annual.iloc[i + 1]
            calm = annual.iloc[i - 1] < annual.iloc[i - 2] * 1.15 and annual.iloc[i - 1] >= annual.iloc[i - 2] * 0.7
            if prev_v > 0 and calm and curr_v >= prev_v * 1.15 and next_v <= curr_v * 0.95:
                annual_adj.iloc[i] = prev_v
                spike_years.append(str(annual.index[i].year))
        annual = annual_adj

        cut_count = 0
        cut_years = []
        for i in range(1, len(annual)):
            prev = annual.iloc[i - 1]
            curr = annual.iloc[i]
            if curr < prev * 0.95:
                cut_count += 1
                cut_years.append(str(annual.index[i].year))
        result["cut_count"] = cut_count
        result["cut_years"] = cut_years

        # 直近連続増配年数（前年比で増えている年が何年続いているか）
        consecutive_increase = 0
        for i in range(len(annual) - 1, 0, -1):
            if annual.iloc[i] > annual.iloc[i - 1]:
                consecutive_increase += 1
            else:
                break
        result["consecutive_increase"] = consecutive_increase

        is_increasing = False
        trend_ratio = None
        if len(annual) >= 3:
            # 前半vs後半の平均比較（直近の下落を無視しないため固定3年比較をやめる）
            mid       = len(annual) // 2
            early_avg = annual.iloc[:mid].mean()
            late_avg  = annual.iloc[mid:].mean()
            if early_avg > 0:
                trend_ratio = round(late_avg / early_avg, 3)
            trend_up  = early_avg > 0 and late_avg > early_avg * 1.05
            # 直近年が前年から5%超下落していれば増配傾向とみなさない
            recent_ok = annual.iloc[-1] >= annual.iloc[-2] * 0.95
            # 回復型（2026-11-08追加）: 谷を打ってから3年以上連続で増配し、直近年が
            # 前半のピーク以上に戻していれば増配傾向とみなす。前半/後半の平均比較だけだと
            # 谷の年が後半に含まれるV字回復銘柄（コロナ禍の減配からの回復など）が
            # 一律に「増配傾向なし」になる問題への対処
            recovery  = consecutive_increase >= 3 and annual.iloc[-1] >= annual.iloc[:mid].max()
            if (trend_up or recovery) and recent_ok:
                is_increasing = True
        elif len(annual) >= 2:
            if annual.iloc[-1] > annual.iloc[0] * 1.05:
                is_increasing = True
            if annual.iloc[0] > 0:
                trend_ratio = round(annual.iloc[-1] / annual.iloc[0], 3)
        result["is_increasing"] = is_increasing
        result["trend_ratio"]   = trend_ratio

        # 直近の非減配継続年数（5%超の下落がない状態が何年続いているか、年数ベース）
        noncut_steps = 0
        for i in range(len(annual) - 1, 0, -1):
            if annual.iloc[i] >= annual.iloc[i - 1] * 0.95:
                noncut_steps += 1
            else:
                break
        result["consecutive_noncut"] = min(noncut_steps + 1, len(annual))

        # 直前の完了年が減配だったか
        result["last_year_cut"] = len(annual) >= 2 and annual.iloc[-1] < annual.iloc[-2] * 0.95

        # 特別配当の疑い：単年だけ突出（前年の1.5倍超）し、翌年に元の水準へ戻る年
        suspects = []
        for i in range(1, len(annual) - 1):
            prev_v, curr_v, next_v = annual.iloc[i - 1], annual.iloc[i], annual.iloc[i + 1]
            if prev_v > 0 and curr_v > prev_v * 1.5 and next_v < curr_v * 0.7 and next_v <= prev_v * 1.15:
                suspects.append(str(annual.index[i].year))
        result["special_suspect_years"] = sorted(set(suspects) | set(spike_years))

        detail = "配当" + str(len(annual)) + "年確認"
        if is_increasing:
            detail += "/増加傾向"
        if cut_years:
            detail += "/減配:" + ",".join(cut_years)
        result["detail"] = detail
        return result

    except Exception as e:
        result["detail"] = "配当履歴取得失敗"
        return result

def make_div_history_str(cut_count, years_checked, is_increasing):
    if years_checked == 0:
        return "-"
    if cut_count == 0:
        if years_checked >= 10:
            return "👑" + str(years_checked) + "年連続"
        return str(years_checked) + "年連続"
    elif cut_count == 1:
        return "減配1回(増配傾向)" if is_increasing else "減配1回"
    else:
        return "減配" + str(cut_count) + "回(増配傾向)" if is_increasing else "減配" + str(cut_count) + "回"

def make_eps_str(t_eps, f_eps):
    if t_eps and t_eps != 0:
        t_r = round(t_eps, 1)
        if f_eps and f_eps != 0:
            f_r = round(f_eps, 1)
            g = round((f_eps - t_eps) / abs(t_eps) * 100, 1)
            prefix = "+" if g >= 0 else ""
            return str(t_r) + "→" + str(f_r) + "円(" + prefix + str(g) + "%)"
        return str(t_r) + "円(予想なし)"
    return "-"

def get_payout_ratio(info, ticker, div=None):
    """(配当性向, 計算不能理由) を返す。計算できれば (値, "")。div は年間1株配当（解決済みなら渡す）"""
    f_eps = info.get("forwardEps")
    t_eps = info.get("trailingEps")

    # trailingEps（実績値）を優先。forwardEpsを先に使うと
    # 直近赤字等の銘柄で配当性向が低く見えてしまう。
    if t_eps is not None and t_eps > 0:
        eps = t_eps
    elif f_eps is not None and f_eps > 0:
        eps = f_eps
    elif t_eps is not None or f_eps is not None:
        return None, "EPS赤字"
    else:
        return None, "EPSデータ欠損"

    if div is not None:
        div_rate = div
    else:
        div_rate, _src = resolve_annual_dividend(info, ticker, None, fetch_nikkei=False)

    if div_rate <= 0:
        return None, "配当データ欠損"

    payout = round(div_rate / eps * 100, 1)

    if payout <= 0 or payout > 1000:
        return None, "配当性向データ異常"

    return payout, ""

def check_payout_recovery(info):
    t_eps = info.get("trailingEps")
    f_eps = info.get("forwardEps")
    if t_eps is not None and f_eps is not None and t_eps != 0 and f_eps > t_eps:
        pct = round((f_eps - t_eps) / abs(t_eps) * 100, 1)
        return True, "業績回復見込み(予EPS+" + str(pct) + "%)"
    rec = (info.get("recommendationKey") or "").lower()
    if rec in ("buy", "strong_buy"):
        return True, "業績回復見込み(アナリスト買い推奨)"
    rev_g = info.get("revenueGrowth")
    ear_g = info.get("earningsGrowth")
    if rev_g is not None and rev_g > 0.05:
        return True, "業績回復見込み(売上成長+" + str(round(rev_g * 100, 1)) + "%)"
    if ear_g is not None and ear_g > 0.05:
        return True, "業績回復見込み(利益成長+" + str(round(ear_g * 100, 1)) + "%)"
    return False, ""

def get_equity_ratio(info, ticker):
    """自己資本比率(%)。取得できなければ None（0%と区別する）"""
    try:
        bs = ticker.balance_sheet
        if bs is not None and not bs.empty:
            eq_keys = [
                "Stockholders Equity",
                "Total Stockholder Equity",
                "Common Stock Equity",
                "Total Equity Gross Minority Interest"
            ]
            asset_keys = ["Total Assets"]

            eq_val    = None
            asset_val = None

            for k in eq_keys:
                if k in bs.index:
                    eq_val = bs.loc[k].iloc[0]
                    break

            for k in asset_keys:
                if k in bs.index:
                    asset_val = bs.loc[k].iloc[0]
                    break

            if eq_val is not None and asset_val is not None and asset_val > 0:
                return round(eq_val / asset_val * 100, 1)
    except:
        pass

    return None


def get_fcf_coverage(ticker, info, div_rate):
    """FCFカバレッジ = FCF平均（最大4期） ÷ 直近期の年間配当総額。
    設備投資の単年ブレに引きずられないよう FCF は複数期平均を使う。
    取得できなければ None。FCF = 営業CF + 設備投資（yfinanceでは負値）"""
    try:
        cf = ticker.cashflow
        if cf is None or cf.empty:
            return None

        def _col(names, idx):
            for n in names:
                if n in cf.index:
                    v = cf.loc[n].iloc[idx]
                    if v is not None and not pd.isna(v):
                        return float(v)
            return None

        OCF_NAMES  = ["Operating Cash Flow", "Total Cash From Operating Activities",
                      "Cash Flow From Continuing Operating Activities"]
        CAP_NAMES  = ["Capital Expenditure", "Capital Expenditures"]
        DIV_NAMES  = ["Cash Dividends Paid", "Common Stock Dividend Paid",
                      "Cash Dividend Paid"]

        fcf_list = []
        for idx in range(min(4, len(cf.columns))):
            ocf   = _col(OCF_NAMES, idx)
            capex = _col(CAP_NAMES, idx)
            if ocf is not None and capex is not None:
                fcf_list.append(ocf + capex)
        if not fcf_list:
            return None
        fcf_avg = sum(fcf_list) / len(fcf_list)

        div_paid = _col(DIV_NAMES, 0)
        if div_paid is None or div_paid == 0:
            shares = info.get("sharesOutstanding") or 0
            if div_rate and shares:
                div_paid = -abs(div_rate * shares)
            else:
                return None
        div_total = abs(div_paid)
        if div_total <= 0:
            return None
        return round(fcf_avg / div_total, 2)
    except Exception:
        return None


def compute_axis_scores(d):
    """4軸スコア（各25点満点）を計算する。
    d のキー: cut_count, years_checked, is_increasing, trend_ratio,
    consecutive_increase, consecutive_noncut, payout, fcf_cov,
    eq_ratio, de_ratio, roe, is_finance, rev_g, ear_g, t_eps, f_eps, dy
    データ欠損は 0点ではなく中立点（小項目の半分）を原則とする。"""
    # ---- 軸1: 配当持続性 ----
    s = 0.0
    cc = d["cut_count"]
    s += 10 if cc == 0 else (6 if cc == 1 else 2)
    if d["years_checked"] == 0:
        s += 2.5
    else:
        nc = d["consecutive_noncut"]
        s += 5 if nc >= 10 else (4 if nc >= 5 else (3 if nc >= 3 else (1 if nc >= 1 else 0)))
    p = d["payout"]
    s += 6 if p <= 60 else (4 if p <= 75 else (2 if p <= 90 else 0))
    fcf = d["fcf_cov"]
    if fcf is None:
        s += 2
    else:
        s += 4 if fcf >= 1.5 else (3 if fcf >= 1.0 else (1 if fcf >= 0.5 else 0))
    sustain = s
    # FCFカバレッジが弱い銘柄は持続性をキャップする（配当がフリーキャッシュフローで
    # 裏付けられていない銘柄は、他の指標が良くても高得点にしない。2026-11-07追加）
    if fcf is not None:
        if fcf < 0.5:
            sustain = min(sustain, 12)
        elif fcf < 1.0:
            sustain = min(sustain, 18)

    # ---- 軸2: 財務健全性 ----
    f = 0.0
    eq, fin = d["eq_ratio"], d["is_finance"]
    if fin or eq is None:
        f += 5  # 金融は構造的に低いため中立点。取得失敗時も中立点
    else:
        f += 10 if eq >= 60 else (7 if eq >= 40 else (4 if eq >= 30 else (2 if eq >= 20 else 0)))
    de = d["de_ratio"]
    if fin or de is None:
        f += 4
    else:
        f += 8 if de <= 0.5 else (5 if de <= 1.0 else (3 if de <= 2.0 else 0))
    roe = d["roe"]
    if roe is None:
        f += 3.5
    else:
        f += 7 if roe >= 10 else (5 if roe >= 8 else (3 if roe >= 5 else 0))
    finance = f

    # ---- 軸3: 成長性 ----
    g = 0.0
    if d["years_checked"] < 2:
        g += 4
    elif d["is_increasing"]:
        g += 8
    elif d["trend_ratio"] is not None and d["trend_ratio"] >= 0.95:
        g += 4
    te, fe = d["t_eps"], d["f_eps"]
    if te is not None and fe is not None and te > 0:
        gr = (fe - te) / te * 100
        g += 6 if gr >= 10 else (4 if gr >= 0 else (2 if gr >= -10 else 0))
    elif te is not None and fe is not None and te <= 0 < fe:
        g += 6  # 黒字転換見込み
    else:
        g += 3
    rg = d["rev_g"]
    if rg is None:
        g += 3
    else:
        rp = rg * 100
        g += 6 if rp >= 5 else (4 if rp >= 0 else (2 if rp >= -5 else 0))
    eg = d["ear_g"]
    if eg is None:
        g += 2.5
    else:
        ep = eg * 100
        g += 5 if ep >= 5 else (3 if ep >= 0 else (1 if ep >= -5 else 0))
    growth = g

    # ---- 軸4: 還元魅力 ----
    r = 0.0
    dy = d["dy"]
    r += 10 if dy >= 4.5 else (8 if dy >= 4.0 else (6 if dy >= 3.5 else (4 if dy >= 3.0 else 2)))
    if 40 <= p <= 70:
        r += 6
    elif 30 <= p < 40 or 70 < p <= 80:
        r += 4
    elif 20 <= p < 30 or p > 80:
        r += 2
    ci = d["consecutive_increase"]
    r += 5 if ci >= 5 else (4 if ci >= 3 else (2 if ci >= 1 else 0))
    tr = d["trend_ratio"]
    if tr is None:
        r += 2
    else:
        r += 4 if tr >= 1.3 else (3 if tr >= 1.15 else (2 if tr >= 1.05 else 0))
    return_ = r

    return sustain, finance, growth, return_

def fetch_info_retry(symbol):
    session = get_robust_session()

    for attempt in range(MAX_RETRY):
        try:
            time.sleep(INFO_WAIT + random.uniform(0.5, 1.5))
            ticker = yf.Ticker(symbol, session=session)
            info   = ticker.info
            
            # データが空っぽの場合も、エラーとして投げてリトライさせる
            if not info or len(info) < 5:
                raise ValueError("info is empty")
                
            return info, ticker
        except Exception as e:
            msg = str(e)
            # 429(制限)だけでなく、401(Crumbエラー)や空データの場合も粘り強くリトライする
            if "429" in msg or "Too Many" in msg or "401" in msg or "Crumb" in msg or "info is empty" in msg:
                wait = (2 ** attempt) * 5
                print(f"retry {symbol} wait {wait}s (reason: {msg[:30]})")
                time.sleep(wait)
                # エラー時はセッション（ブラウザ）を新しいものにリセットして再突撃
                session = get_robust_session()
            else:
                print(f"error {symbol} {msg[:60]}")
                return None, None
    return None, None

def get_nikkei_forecast_dividend(code4):
    """日経の会社ページから予想配当利回りと現在値を取得し、年間予想配当（円/株）を推定して返す。
    戻り値: (年間予想配当, 予想配当利回り) / 取得失敗時は (None, None)
    ※ 日経の予想配当利回りは会社予想ベースで、株探の表示利回りとも一致することを確認済み（2026-11-07）。
    """
    try:
        sess = get_robust_session()
        time.sleep(0.5 + random.uniform(0.1, 0.4))
        resp = sess.get(f"https://www.nikkei.com/nkd/company/?scode={code4}", timeout=15)
        if resp.status_code != 200:
            print(f"    nikkei div {code4}: HTTP {resp.status_code}")
            return None, None
        html = resp.text
        ym = re.search(r"予想配当利回り</a>.*?<span class=\"m-stockInfo_detail_value\">([\d.]+)", html, re.S)
        pm = re.search(r"<dd class=\"m-stockPriceElm_value now\">([\d,]+(?:\.\d+)?)<span", html)
        if not ym or not pm:
            print(f"    nikkei div {code4}: parse failed")
            return None, None
        nk_yield = float(ym.group(1))
        nk_price = float(pm.group(1).replace(",", ""))
        if nk_yield <= 0 or nk_price <= 0:
            return None, None
        return round(nk_yield * nk_price / 100, 2), nk_yield
    except Exception as e:
        print(f"    nikkei div {code4}: error {str(e)[:60]}")
        return None, None


def resolve_annual_dividend(info, ticker, code4, fetch_nikkei=True):
    """年間1株配当（円）を決定し、(配当額, 出所) を返す。決まらなければ (0, "")。
    優先順位: 日経の予想配当（会社予想ベース）→ Yahoo dividendRate（実績との乖離ガード付き）
              → Yahoo trailingAnnualDividendRate → 配当実績の直近1年合計
    ※ Yahoo の dividendRate は銘柄により実績と大きく乖離する（例: 6136 オーエスジーで
      実績71円に対し182円、2026-11-07 実測）ため、主ソースにせず照合して使う。
    """
    if fetch_nikkei and code4:
        div, _nk_yield = get_nikkei_forecast_dividend(code4)
        if div and div > 0:
            return div, "nikkei"
    div_rate = info.get("dividendRate") or 0
    trailing = info.get("trailingAnnualDividendRate") or 0
    if div_rate > 0:
        if trailing > 0 and div_rate > trailing * 1.5:
            # Yahoo の予想が実績と大きく乖離 → 実績ベースにフォールバック
            return trailing, "yahoo-guard"
        return div_rate, "yahoo"
    if trailing > 0:
        return trailing, "yahoo-trailing"
    try:
        divs = ticker.dividends
        if divs is not None and len(divs) > 0:
            divs.index = divs.index.tz_localize(None) if divs.index.tzinfo else divs.index
            cutoff = pd.Timestamp.now() - pd.DateOffset(years=1)
            s = divs[divs.index >= cutoff].sum()
            if s > 0:
                return float(s), "yahoo-divs"
    except Exception:
        pass
    return 0, ""


def get_dividend_yield(info, ticker, price, div=None):
    """利回りを計算。div（年間1株配当）が渡されればそれを使う。
    div がなければ従来通り Yahoo の dividendRate 等から計算する（一次判定用。
    ここでは乖離ガードを適用しない: ガード適用後の値で足切り判定すると、
    日経での再確定が必要な銘柄まで落としてしまうため）"""
    if div is not None:
        return round(div / price * 100, 2) if div > 0 else 0.0
    div_rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate") or 0
    if div_rate > 0:
        return round(div_rate / price * 100, 2)
    try:
        divs = ticker.dividends
        if divs is not None and len(divs) > 0:
            divs.index = divs.index.tz_localize(None) if divs.index.tzinfo else divs.index
            cutoff = pd.Timestamp.now() - pd.DateOffset(years=1)
            recent = divs[divs.index >= cutoff].sum()
            if recent > 0:
                return round(recent / price * 100, 2)
    except:
        pass
    return 0.0

def _parse_yutai_html(html):
    """
    HTML から (shares_text, month_text) を抽出。
    構造化データ（th/td, dt/dd）のみを対象とし、
    ナビゲーション等の汎用テキストに引っかからないよう厳格に判定する。
    """
    soup = BeautifulSoup(html, "html.parser")
    month_text = ""
    shares_text = ""

    # th→td / dt→dd の両方を試す
    for label_tag, value_tag in [("th", "td"), ("dt", "dd")]:
        for label in soup.find_all(label_tag):
            txt = label.get_text(strip=True)
            val_el = label.find_next_sibling(value_tag)
            if val_el is None:
                parent = label.parent
                next_row = parent.find_next_sibling("tr") if parent else None
                if next_row:
                    val_el = next_row.find(value_tag)
            if val_el is None:
                continue
            val = val_el.get_text(" ", strip=True)

            if any(k in txt for k in ["権利確定", "権利月", "確定月"]):
                months = re.findall(r'\d+月', val)
                if months:
                    month_text = "・".join(dict.fromkeys(months))

            # 株主優待専用ラベルのみ（「単元株数」「売買単位」等の売買単位ラベルは全銘柄に存在するため除外）
            YUTAI_SHARE_LABELS = ["権利株数", "最低保有株数", "必要保有株数", "保有株数条件",
                                   "優待最低株数", "受取株数"]
            if not shares_text and any(k in txt for k in YUTAI_SHARE_LABELS):
                nums = re.findall(r'[\d,]+', val)
                if nums:
                    n = int(nums[0].replace(",", ""))
                    if 1 <= n <= 100000:
                        shares_text = f"{n}株以上"

    # regex フォールバック: "権利確定月" の直後のみ月を探す（ナビ誤検知防止）
    if not month_text:
        m = re.search(r'権利確定月[^月\d]{0,20}(\d+)月', html)
        if m:
            month_text = m.group(1) + "月"

    # 株数は "1単元(100株)" という明示的パターンのみ
    if not shares_text:
        m = re.search(r'1単元[（(](\d[\d,]*)株[）)]', html)
        if m:
            n = int(m.group(1).replace(",", ""))
            if 1 <= n <= 10000:
                shares_text = f"{n}株以上"

    return shares_text, month_text


def _parse_nikkei_yutai(html):
    """
    日経の株主優待ページをパースし、(最低株数テキスト, 権利確定月テキスト) を返す。
    日経は「権利確定 2・8月」のように複数月をまとめて表記する。
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    months = []
    for m in re.finditer(r"権利確定\s*([0-9]{1,2}(?:・[0-9]{1,2})*)月", text):
        for part in m.group(1).split("・"):
            v = int(part)
            if 1 <= v <= 12 and v not in months:
                months.append(v)
    months.sort()
    month_text = "・".join(str(v) + "月" for v in months)

    shares_text = ""
    nums = []
    for m in re.finditer(r"([0-9][0-9,]*)株以上", text):
        n = int(m.group(1).replace(",", ""))
        if 1 <= n <= 100000:
            nums.append(n)
    if nums:
        shares_text = f"{min(nums):,}株以上"

    return shares_text, month_text


def get_yutai(code4):
    """
    株主優待情報を取得。日経 → kabutan.jp → minkabu.jp の順で試みる。
    - 明示的に「なし」と確認できた場合のみ「なし」を返す
    - パース成功時は「XXX株以上 / X月」形式
    - 判定できなかった場合は「-」（不明）を返す
    ※ 「優待」「権利」がページ内に存在するだけでは「あり」と判定しない
    """
    NO_YUTAI = [
        "株主優待は実施していません",
        "株主優待制度はございません",
        "株主優待制度はありません",
        "株主優待はありません",
        "株主優待情報はありません",
        "この企業には株主優待の情報がありません",
        "株主優待の設定はありません",
        "優待制度を実施しておりません",
        "優待制度はございません",
        "株主優待はございません",
    ]
    urls = [
        ("nikkei", f"https://www.nikkei.com/nkd/company/yutai/?scode={code4}"),
        ("kabutan", f"https://kabutan.jp/stock/yutai?code={code4}"),
        ("minkabu", f"https://minkabu.jp/stock/{code4}/yutai"),
    ]
    for source, url in urls:
        try:
            sess = get_robust_session()
            time.sleep(0.8 + random.uniform(0.2, 0.6))
            resp = sess.get(url, timeout=15)
            print(f"    yutai {code4}: HTTP {resp.status_code} ({url})")
            if resp.status_code != 200:
                continue

            html = resp.text

            # 日経は形式が異なるため専用に処理する
            if source == "nikkei":
                soup_text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
                if "該当する企業の優待情報がありません" in soup_text:
                    print(f"    yutai {code4}: なし(明示・日経)")
                    return "なし"
                shares_text, month_text = _parse_nikkei_yutai(html)
                if month_text:
                    parts = [p for p in [shares_text, month_text] if p]
                    result = " / ".join(parts)
                    print(f"    yutai {code4}: {result} (日経)")
                    return result
                print(f"    yutai {code4}: 月情報なし、次URL試行")
                continue

            # 「なし」が明示されている場合
            if any(marker in html for marker in NO_YUTAI):
                print(f"    yutai {code4}: なし(明示)")
                return "なし"

            # 構造化データを厳格にパース
            shares_text, month_text = _parse_yutai_html(html)

            # 権利確定月は yutai 固有情報なので月あり = 確実にあり
            # 株数のみ（月なし）は単元株数の誤検知リスクが高いため不採用
            if month_text:
                parts = [p for p in [shares_text, month_text] if p]
                result = " / ".join(parts)
                print(f"    yutai {code4}: {result}")
                return result

            # ページは取得できたが判定不可 → 次のURLへ
            print(f"    yutai {code4}: 月情報なし、次URL試行")

        except Exception as e:
            print(f"    yutai error {code4}: {str(e)[:60]}")
            continue

    # 全URL失敗 or 判定不可
    return "-"

def analyze(symbol, industry, forced=False):
    info, ticker = fetch_info_retry(symbol)
    if info is None:
        print("    reason: info failed")
        return {"excluded": True, "reason": "データ取得失敗", "dy": 0}

    price = info.get("currentPrice") or info.get("previousClose") or 0
    if price == 0:
        print("    reason: no price")
        return {"excluded": True, "reason": "株価データなし", "dy": 0}

    # 時価総額の足切り（2026-11-08追加）: 規模の下限未満は長期高配当投資の対象外として
    # 除外記録に残す。marketCap が取得できない場合はデータ欠損として足切りしない。
    mcap_raw = info.get("marketCap") or 0
    if mcap_raw:
        mcap_oku_chk = round(mcap_raw / 100000000)
        if mcap_oku_chk < MIN_MCAP_OKU:
            print("    reason: small mcap=" + str(mcap_oku_chk))
            return {"excluded": True, "reason": "時価総額" + str(mcap_oku_chk) + "億円（基準未満）", "dy": 0}

    code4 = symbol.replace(".T", "")
    dy = get_dividend_yield(info, ticker, price)

    # 案B（2026-11-07）: 足切り近辺以上の銘柄は、日経の予想配当（会社予想ベース）で
    # 年間配当を確定し、利回り・配当性向・FCFカバレッジをその配当額で統一計算する。
    div = None
    div_src = ""
    if forced or dy >= MIN_YIELD - 0.5:
        div, div_src = resolve_annual_dividend(info, ticker, code4)
        if div:
            dy = round(div / price * 100, 2)

    if dy > 30:
        print("    reason: abnormal yield=" + str(dy))
        return {"excluded": True, "reason": "利回り異常値=" + str(dy) + "%", "dy": dy}

    if not forced and dy < MIN_YIELD:
        print("    reason: low yield=" + str(dy))
        return {"excluded": True, "reason": "利回り" + str(dy) + "%<" + str(MIN_YIELD) + "%", "dy": dy}

    reasons = []

    if div_src == "yahoo-guard":
        reasons.append("利回りは実績配当ベース(Yahoo予想配当と乖離大)")
    elif div_src.startswith("yahoo"):
        reasons.append("配当はYahooベース(日経取得失敗)")

    if forced and dy < MIN_YIELD:
        reasons.append("利回り" + str(dy) + "%(低め)")

    hist = check_dividend_history(ticker)
    cut_count            = hist["cut_count"]
    years_checked        = hist["years_checked"]
    is_increasing        = hist["is_increasing"]
    consecutive_increase = hist["consecutive_increase"]
    last_year_cut        = hist["last_year_cut"]
    cut_years            = hist["cut_years"]
    div_history = make_div_history_str(cut_count, years_checked, is_increasing)
    div_trend   = json.dumps(hist["annual_list"]) if hist["annual_list"] else "[]"

    _cut_yr_str = "(" + "・".join(cut_years) + "年)" if cut_years else ""
    if cut_count >= 2:
        if is_increasing:
            note = "減配歴" + str(cut_count) + "回" + _cut_yr_str + "(増配傾向継続)"
            if consecutive_increase > 0:
                note += "/連続増配" + str(consecutive_increase) + "年"
            reasons.append(note)
            print("    div cut " + str(cut_count) + " but increasing trend: include")
        else:
            print("    reason: div cut " + str(cut_count) + " times no increasing trend")
            return {"excluded": True, "reason": "減配" + str(cut_count) + "回" + _cut_yr_str + "・増配傾向なし", "dy": dy}
    elif cut_count == 1:
        note = "減配歴1回" + _cut_yr_str
        if consecutive_increase > 0:
            note += "/連続増配" + str(consecutive_increase) + "年"
        reasons.append(note)
    if years_checked == 0:
        reasons.append("配当歴取得不可")
    elif 0 < years_checked < 10:
        reasons.append("配当歴" + str(years_checked) + "年(10年未満)")
    if hist["special_suspect_years"]:
        reasons.append("特別配当の可能性(" + "・".join(hist["special_suspect_years"]) + "年)")

    # 記念・特別配当の評価（2026-11-14追加）: 今期予想に一時的な増配（記念配当等）が
    # 含まれる疑いがある場合、評価（利回り・性向・FCF・スコア）は普通配当ベース
    # （前期水準）で行い、記念配当自体は備考表示のみとする（本人の方針）。
    # 現年分は落下が未確認のため、本物の大幅増配（例: ゴールドウインの+20.7%）を
    # 巻き込まないよう1.5倍以上の極端なジャンプに限定する。履歴・剥落側は
    # 静穏＋落下確認の条件つきで1.15倍から判定する。
    eval_div = div
    spike_current = False
    if div and hist["annual_list"]:
        _prev_fy = hist["annual_list"][-1][1]
        _calm_now = len(hist["annual_list"]) < 3 or (hist["annual_list"][-2][1] < hist["annual_list"][-3][1] * 1.15 and hist["annual_list"][-2][1] >= hist["annual_list"][-3][1] * 0.7)
        if _prev_fy and _prev_fy > 0 and div >= _prev_fy * 1.5 and _calm_now:
            spike_current = True
            eval_div = _prev_fy
            dy = round(eval_div / price * 100, 2)
            reasons.append("今期配当に記念配当等の一時的増配の疑い(評価は普通配当ベース)")
            if not forced and dy < MIN_YIELD:
                print("    reason: ordinary-base yield=" + str(dy))
                return {"excluded": True, "reason": "利回り" + str(dy) + "%(普通配当ベース)<" + str(MIN_YIELD) + "%", "dy": dy}

    # 今期配当チェック：今期予想（解決済み div）を前期の期別実績と比較する（2026-11-09修正）
    # 旧方式は暦年の年途中合計同士の比較で、中間配当の導入（全国保証7164）や
    # 分割が絡むと増配でも「減配確定」になる誤判定があった。
    # 減配確定は除外、前期減配+今期増配未確認も除外（後段の last_year_cut 判定）。
    curr_year_increase = False
    try:
        if div and hist["annual_list"]:
            _prev_fy_div = hist["annual_list"][-1][1]
            if _prev_fy_div > 0:
                chg = (eval_div - _prev_fy_div) / _prev_fy_div * 100
                if chg >= 5:
                    curr_year_increase = True
                    reasons.append("今期増配確定(" + str(round(eval_div, 1)) + "円/前期比+" + str(round(chg, 1)) + "%)")
                elif chg <= -1:
                    # 記念配当等の剥落チェック（2026-11-14追加）: 前期が一時的な増配
                    # （記念配当等）で跳ねていた場合、その分が落ちるだけの減は減配と
                    # みなさない。今期予想が前々期（普通配当ベース）以上なら除外しない。
                    _reclass = False
                    if len(hist["annual_list"]) >= 2:
                        _base_div = hist["annual_list"][-2][1]
                        _calm_b = len(hist["annual_list"]) < 3 or (_base_div < hist["annual_list"][-3][1] * 1.15 and _base_div >= hist["annual_list"][-3][1] * 0.7)
                        if _base_div > 0 and _prev_fy_div >= _base_div * 1.15 and _calm_b and div >= _base_div:
                            _chg_b = (div - _base_div) / _base_div * 100
                            reasons.append("今期配当は前期の記念配当等の剥落を含む(普通配当ベースでは前々期比+" + str(round(_chg_b, 1)) + "%)")
                            if _chg_b >= 5:
                                curr_year_increase = True
                            _reclass = True
                    if not _reclass:
                        if chg <= -5:
                            print("    reason: current FY dividend cut")
                            return {"excluded": True, "reason": "今期減配確定(" + str(round(eval_div, 1)) + "円/前期比" + str(round(chg, 1)) + "%)", "dy": dy}
                        # -5%〜-1%の減少：除外はしないが備考に記載
                        reasons.append("今期減配予定(" + str(round(eval_div, 1)) + "円/前期比" + str(round(chg, 1)) + "%)")
                else:
                    # -1%未満の変動は日経予想の逆算誤差の範囲として据え置き扱い
                    reasons.append("今期配当確定(" + str(round(eval_div, 1)) + "円)")
    except Exception:
        pass

    if last_year_cut and not curr_year_increase:
        prev_yr = pd.Timestamp.now().year - 1
        print("    reason: " + str(prev_yr) + "年減配・今年増配未確認")
        return {"excluded": True, "reason": str(prev_yr) + "年減配・今年増配未確認", "dy": dy}

    t_eps = info.get("trailingEps")
    f_eps = info.get("forwardEps")
    eps_str = make_eps_str(t_eps, f_eps)

    payout, payout_err = get_payout_ratio(info, ticker, eval_div if eval_div else None)
    if payout is None:
        print("    reason: payout cannot be calculated: " + payout_err)
        return {"excluded": True, "reason": "配当性向計算不可(" + payout_err + ")", "dy": dy}

    if payout > 100:
        print("    reason: payout=" + str(round(payout)) + "% over EPS")
        return {"excluded": True, "reason": "配当性向" + str(round(payout)) + "%(EPS超過配当)", "dy": dy}

    if payout > 70:
        ok, rec_note = check_payout_recovery(info)
        if ok:
            reasons.append("配当性向" + str(round(payout)) + "%(高水準)")
            reasons.append(rec_note)
        else:
            reasons.append("配当性向" + str(round(payout)) + "%(高水準)")

    # スコア用配当性向: 前方（予想EPSベース）も見て、厳しい方を採用する。
    # 実績EPSだけだと利益減速局面で性向が低く見え、増配の持続性を過大評価するため（2026-11-07追加）
    fwd_payout = None
    div_for_fwd = eval_div if eval_div else 0
    if not div_for_fwd:
        div_for_fwd, _fs = resolve_annual_dividend(info, ticker, None, fetch_nikkei=False)
    if f_eps and f_eps > 0 and div_for_fwd:
        fwd_payout = round(div_for_fwd / f_eps * 100, 1)
    payout_score = max(payout, fwd_payout) if fwd_payout is not None else payout
    if fwd_payout is not None and fwd_payout > 80:
        reasons.append("前方配当性向" + str(fwd_payout) + "%(高水準)")

    if info.get("revenueGrowth") is None and info.get("earningsGrowth") is None:
        reasons.append("成長データ不明")

    is_finance = any(x in industry for x in ["銀行", "保険", "証券", "その他金融"])
    eq_ratio   = get_equity_ratio(info, ticker)

    div_rate_for_fcf = eval_div if eval_div else (info.get("dividendRate") or info.get("trailingAnnualDividendRate") or (dy * price / 100 if dy > 0 else 0))
    fcf_cov = get_fcf_coverage(ticker, info, div_rate_for_fcf)
    if fcf_cov is not None and fcf_cov < 0.5:
        reasons.append("FCFカバレッジ低(" + str(fcf_cov) + "倍)")
    if eq_ratio is not None and not is_finance and eq_ratio < 20:
        reasons.append("自己資本" + str(eq_ratio) + "%(低)")

    de_raw  = info.get("debtToEquity")
    de_ratio = round(de_raw / 100, 3) if de_raw is not None else None
    roe_raw = info.get("returnOnEquity")
    roe_pct = round(roe_raw * 100, 1) if roe_raw is not None else None

    sustain, finance_s, growth, return_s = compute_axis_scores({
        "cut_count": cut_count,
        "years_checked": years_checked,
        "is_increasing": is_increasing,
        "trend_ratio": hist["trend_ratio"],
        "consecutive_increase": consecutive_increase,
        "consecutive_noncut": hist["consecutive_noncut"],
        "payout": payout_score,
        "fcf_cov": fcf_cov,
        "eq_ratio": eq_ratio,
        "de_ratio": de_ratio,
        "roe": roe_pct,
        "is_finance": is_finance,
        "rev_g": info.get("revenueGrowth"),
        "ear_g": info.get("earningsGrowth"),
        "t_eps": t_eps,
        "f_eps": f_eps,
        "dy": dy,
    })
    total = int(round(sustain + finance_s + growth + return_s))
    star  = max(1, min(5, int(total / 20 + 0.5)))
    judge = "〇" if star >= 4 else ("△" if star >= 2 else "×")

    m_cap = info.get("marketCap") or 0
    code4 = symbol.replace(".T", "")
    yutai = get_yutai(code4)

    return {
        "excluded":    False,
        "dy":          dy,
        "payout":      round(payout, 1),
        "eq":          eq_ratio if eq_ratio is not None else 0,
        "mcap":        round(m_cap / 100000000) if m_cap else 0,
        "judge":       judge,
        "stars":       "★" * star + "☆" * (5 - star),
        "note":        " / ".join(reasons) if reasons else "良好(指標クリア)",
        "score":       total,
        "score_sustain": round(sustain, 1),
        "score_finance": round(finance_s, 1),
        "score_growth":  round(growth, 1),
        "score_return":  round(return_s, 1),
        "m_cap":       m_cap,
        "yutai":       yutai,
        "div_history": div_history,
        "eps_str":     eps_str,
        "div_trend":   div_trend,
    }

def scan_sector(rows, industry, col_map, forced=False):
    candidates = []
    exclusions = []
    for _, row in rows.iterrows():
        raw    = str(row[col_map["code"]]).strip()
        digits = "".join(filter(str.isdigit, raw))
        if len(digits) < 4:
            continue
        code4  = digits[-4:].zfill(4)
        symbol = code4 + ".T"
        name   = row[col_map["name"]]
        print("  checking " + symbol + " " + name)
        res = analyze(symbol, industry, forced)
        if res is None:
            excl = {"code": int(code4), "name": name, "industry": industry,
                    "reason": "データ取得失敗", "dy": 0}
            exclusions.append(excl)
        elif res.get("excluded"):
            res["code"]     = int(code4)
            res["name"]     = name
            res["industry"] = industry
            exclusions.append(res)
            print("  -> excluded: " + symbol + " " + name + " / " + res.get("reason", ""))
        else:
            res["industry"] = industry
            res["code"]     = int(code4)
            res["name"]     = name
            candidates.append(res)
    return candidates, exclusions

def main():
    started_at = now_jst()
    scan_id    = now_jst_id()

    os.makedirs("logs", exist_ok=True)

    print("=== scan start " + started_at + " ===")
    print("scan_id: " + scan_id)

    init_db()
    set_status("pid",      str(os.getpid()))
    set_status("state",    "running")
    set_status("progress", "0")
    set_status("current",  "準備中")
    set_status("started",  started_at)
    set_status("scan_id",  scan_id)
    update_history(scan_id, started_at, "", 0, "running")
    clear_scan_results(scan_id)

    try:
        df, col_map = fetch_jpx_prime()
    except Exception as e:
        print("ERROR: fetch_jpx_prime failed: " + str(e))
        set_status("state",   "done")
        update_history(scan_id, started_at, now_jst(), 0, "error")
        return

    missing = [k for k in ["market", "industry", "code", "name"] if k not in col_map]
    if missing:
        print("ERROR: missing columns in JPX data: " + str(missing))
        set_status("state",   "done")
        update_history(scan_id, started_at, now_jst(), 0, "error")
        return

    jpx_df = df[df[col_map["market"]].astype(str).str.contains("プライム")].copy()

    jpx_df[col_map["code"]] = (
        jpx_df[col_map["code"]]
        .astype(str).str.strip()
        .str.extract(r"(\d{4})", expand=False)
    )
    jpx_df = jpx_df.dropna(subset=[col_map["code"]])
    jpx_df[col_map["code"]] = jpx_df[col_map["code"]].astype(int)

    shosha_df      = jpx_df[jpx_df[col_map["code"]].isin(SOGO_SHOSHA_CODES)]
    non_shosha_df  = jpx_df[~jpx_df[col_map["code"]].isin(SOGO_SHOSHA_CODES)]
    all_industries = sorted(non_shosha_df[col_map["industry"]].dropna().unique())

    if len(all_industries) == 0:
        print("ERROR: no industries found in JPX data")
        set_status("state",   "done")
        update_history(scan_id, started_at, now_jst(), 0, "error")
        return

    total       = len(all_industries) + 1
    total_count = 0

    for idx, industry in enumerate(all_industries):
        print("[" + str(idx+1) + "/" + str(total) + "] " + industry)
        set_status("current",  "[" + str(idx+1) + "/" + str(total) + "] " + industry)
        set_status("progress", str(round((idx / total) * 100)))

        sector_df = non_shosha_df[non_shosha_df[col_map["industry"]] == industry]
        targets   = get_sector_targets(sector_df, col_map)

        candidates, excls = scan_sector(targets, industry, col_map, forced=False)
        if not candidates:
            candidates, excls = scan_sector(targets, industry, col_map, forced=True)

        now = now_jst()
        if candidates:
            passed_stocks = sorted(candidates, key=lambda x: (x["dy"], x["score"]), reverse=True)
            new_rows = []
            for r in passed_stocks:
                new_rows.append((
                    scan_id, now, r["industry"], r["code"], r["name"],
                    r["dy"], r["payout"], r["eq"], r["mcap"],
                    r["judge"], r["stars"], r["note"], r["score"], r.get("yutai", "-"),
                    r.get("div_history", "-"), r.get("eps_str", "-"), r.get("div_trend", "[]"),
                    r.get("score_sustain", 0), r.get("score_finance", 0), r.get("score_growth", 0), r.get("score_return", 0)
                ))
            save_results_batch(new_rows, scan_id)
            total_count += len(new_rows)

        if excls:
            log_rows = [(scan_id, now, e["industry"], e["code"], e["name"],
                         e.get("reason", "不明"), e.get("dy", 0)) for e in excls]
            save_log_batch(log_rows, scan_id)

    print("scanning shosha...")
    set_status("current", "商社(総合商社)")
    shosha_targets = get_sector_targets(shosha_df, col_map)
    shosha_cand, shosha_excls = scan_sector(shosha_targets, "商社", col_map, forced=False)
    if not shosha_cand:
        shosha_cand, shosha_excls = scan_sector(shosha_targets, "商社", col_map, forced=True)
    now = now_jst()
    if shosha_cand:
        shosha_rows = []
        for r in sorted(shosha_cand, key=lambda x: (x["dy"], x["score"]), reverse=True):
            shosha_rows.append((
                scan_id, now, r["industry"], r["code"], r["name"],
                r["dy"], r["payout"], r["eq"], r["mcap"],
                r["judge"], r["stars"], r["note"], r["score"], r.get("yutai", "-"),
                r.get("div_history", "-"), r.get("eps_str", "-"), r.get("div_trend", "[]"),
                r.get("score_sustain", 0), r.get("score_finance", 0), r.get("score_growth", 0), r.get("score_return", 0)
            ))
        save_results_batch(shosha_rows, scan_id)
        total_count += len(shosha_rows)
    if shosha_excls:
        log_rows = [(scan_id, now, e["industry"], e["code"], e["name"],
                     e.get("reason", "不明"), e.get("dy", 0)) for e in shosha_excls]
        save_log_batch(log_rows, scan_id)

    finished_at = now_jst()
    set_status("state",    "done")
    set_status("progress", "100")
    set_status("finished", finished_at)
    set_status("current",  "完了(" + str(total_count) + "銘柄)")
    update_history(scan_id, started_at, finished_at, total_count, "done")
    print("=== done: " + str(total_count) + " ===")

if __name__ == "__main__":
    main()
