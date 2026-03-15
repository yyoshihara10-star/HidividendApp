import pandas as pd
import yfinance as yf
import sqlite3
import time
import random
import os
from datetime import datetime, timezone, timedelta

SOGO_SHOSHA_CODES = {8058, 8031, 8001, 8053, 8002, 8015, 2768}
MIN_YIELD  = 3.0
INFO_WAIT  = 1.0
MAX_RETRY  = 3
DB_PATH    = "results.db"

JST = timezone(timedelta(hours=9))

def now_jst():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

def now_jst_id():
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")

def init_db():
    conn = sqlite3.connect(DB_PATH)
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
            score INTEGER
        )
    """)
    existing = [row[1] for row in c.execute("PRAGMA table_info(scan_results)").fetchall()]
    if "scan_id" not in existing:
        c.execute("ALTER TABLE scan_results ADD COLUMN scan_id TEXT")
        print("migrated: added scan_id column")

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
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO scan_status VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def save_results(rows, scan_id):
    if not rows:
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM scan_results WHERE scan_id = ?", (scan_id,))
    conn.executemany("""
        INSERT INTO scan_results
        (scan_id, scanned_at, industry, code, name, yield_pct, payout_pct,
         equity_pct, mcap_oku, judge, stars, note, score)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()
    conn.close()

def update_history(scan_id, started_at, finished_at, count, status):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO scan_history
        (scan_id, started_at, finished_at, result_count, status)
        VALUES (?,?,?,?,?)
    """, (scan_id, started_at, finished_at, count, status))
    conn.commit()
    conn.close()

def fetch_jpx_prime():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
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
    return df, col_map

def get_sector_targets(sector_df, col_map):
    """
    大型株を全件取得し、中型株で最大30社まで補完する。
    APIコール不要で時価総額上位を確実にカバー。
    """
    if "size" not in col_map:
        return sector_df.head(30)

    large  = sector_df[sector_df[col_map["size"]] == "大型株"]
    medium = sector_df[sector_df[col_map["size"]] == "中型株"]
    small  = sector_df[sector_df[col_map["size"]] == "小型株"]

    targets = large.copy()

    remaining = 30 - len(targets)
    if remaining > 0:
        targets = pd.concat([targets, medium.head(remaining)])

    remaining = 30 - len(targets)
    if remaining > 0:
        targets = pd.concat([targets, small.head(remaining)])

    return targets

def check_dividend_history(ticker):
    try:
        divs = ticker.dividends
        if divs is None or len(divs) == 0:
            return 0, 0, "配当履歴なし"

        divs.index = divs.index.tz_localize(None) if divs.index.tzinfo else divs.index
        cutoff = pd.Timestamp.now() - pd.DateOffset(years=10)
        divs   = divs[divs.index >= cutoff]

        if len(divs) == 0:
            return 0, 0, "配当履歴なし"

        annual = divs.resample("YE").sum()
        annual = annual[annual > 0]

        if len(annual) < 2:
            return 0, len(annual), "配当履歴" + str(len(annual)) + "年分"

        years_checked = len(annual)
        cut_count     = 0
        cut_years     = []

        for i in range(1, len(annual)):
            prev = annual.iloc[i - 1]
            curr = annual.iloc[i]
            if curr < prev * 0.95:
                cut_count += 1
                cut_years.append(str(annual.index[i].year))

        detail = "配当" + str(years_checked) + "年確認"
        if cut_years:
            detail += "/減配:" + ",".join(cut_years)

        return cut_count, years_checked, detail

    except Exception as e:
        return 0, 0, "配当履歴取得失敗"

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

def fetch_info_retry(symbol):
    for attempt in range(MAX_RETRY):
        try:
            time.sleep(INFO_WAIT + random.uniform(0, 0.5))
            ticker = yf.Ticker(symbol)
            info   = ticker.info
            if not info or len(info) < 5:
                return None, None
            return info, ticker
        except Exception as e:
            msg = str(e)
            if "429" in msg or "Too Many" in msg:
                wait = (2 ** attempt) * 10
                print("rate limit " + symbol + " wait " + str(wait) + "s")
                time.sleep(wait)
            else:
                print("error " + symbol + " " + msg[:60])
                return None, None
    return None, None

def analyze(symbol, industry, forced=False):
    info, ticker = fetch_info_retry(symbol)
    if info is None:
        print("    reason: info取得失敗")
        return None

    price = info.get("currentPrice") or info.get("previousClose") or 0
    if price == 0:
        print("    reason: 株価データなし")
        return None

    div_rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate") or 0
    dy = round(div_rate / price * 100, 2)

    if dy > 30:
        print("    reason: 異常利回り=" + str(dy))
        return None

    if not forced and dy < MIN_YIELD:
        print("    reason: 利回り不足=" + str(dy) + "%")
        return None

    score   = 5
    reasons = []

    if forced and dy < MIN_YIELD:
        score -= 1
        reasons.append("利回り" + str(dy) + "%(低め)")

    cut_count, years_checked, div_detail = check_dividend_history(ticker)
    if cut_count >= 2:
        print("    reason: 減配" + str(cut_count) + "回(" + div_detail + ")")
        return None
    elif cut_count == 1:
        score -= 1
        reasons.append("減配歴1回(" + div_detail + ")")
    else:
        if years_checked > 0:
            reasons.append(div_detail)

    rev_g = info.get("revenueGrowth")
    ear_g = info.get("earningsGrowth")
    if rev_g is not None:
        if rev_g < 0:
            score -= 1
            reasons.append("売上減(" + str(round(rev_g * 100, 1)) + "%)")
    elif ear_g is not None:
        if ear_g < 0:
            score -= 1
            reasons.append("利益減(" + str(round(ear_g * 100, 1)) + "%)")
    else:
        reasons.append("成長データ不明")

    t_eps = info.get("trailingEps")
    f_eps = info.get("forwardEps")
    if t_eps and f_eps and t_eps != 0:
        eps_growth = round((f_eps - t_eps) / abs(t_eps) * 100, 1)
        prefix = "+" if eps_growth >= 0 else ""
        reasons.append(
            "EPS:" + str(round(t_eps, 1)) + "→" + str(round(f_eps, 1)) + "円(" + prefix + str(eps_growth) + "%)"
        )
    elif t_eps:
        reasons.append("EPS:" + str(round(t_eps, 1)) + "円(予想データなし)")

    payout = info.get("payoutRatio") or 0
    if 0 < payout <= 1.0:
        payout *= 100
    if payout > 70:
        ok, note = check_payout_recovery(info)
        if not ok:
            print("    reason: 配当性向" + str(round(payout)) + "%超・回復見込みなし")
            return None
        score -= 1
        reasons.append("配当性向" + str(round(payout)) + "%(一時的)")
        reasons.append(note)
    elif 0 < payout < 30:
        score -= 1
        reasons.append("配当性向" + str(round(payout)) + "%(低)")
    elif payout == 0:
        reasons.append("性向データなし")

    is_finance = any(x in industry for x in ["銀行", "保険", "証券", "その他金融"])
    eq_ratio   = 0.0
    dte = info.get("debtToEquity")
    if dte is not None and dte >= 0:
        eq_ratio = round(100 / (1 + dte / 100), 1)
        if not is_finance and eq_ratio < 40:
            score -= 1
            reasons.append("自己資本" + str(eq_ratio) + "%")

    m_cap = info.get("marketCap") or 0
    star  = max(1, score)
    judge = "〇" if star >= 4 else ("△" if star >= 2 else "×")

    return {
        "dy":    dy,
        "payout": round(payout, 1),
        "eq":    eq_ratio,
        "mcap":  round(m_cap / 100000000) if m_cap else 0,
        "judge": judge,
        "stars": "★" * star + "☆" * (5 - star),
        "note":  " / ".join(reasons) if reasons else "良好(指標クリア)",
        "score": star,
        "m_cap": m_cap,
    }

def scan_sector(rows, industry, col_map, forced=False):
    candidates = []
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
        if res:
            res["industry"] = industry
            res["code"]     = int(code4)
            res["name"]     = name
            candidates.append(res)
        else:
            print("  -> excluded: " + symbol + " " + name)
    return candidates

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

    try:
        df, col_map = fetch_jpx_prime()
        print("JPX columns: " + str(list(df.columns)))
        print("col_map: " + str(col_map))
        print("JPX rows: " + str(len(df)))
    except Exception as e:
        print("JPX fetch error: " + str(e))
        set_status("state",   "done")
        set_status("current", "失敗:JPXデータ取得エラー")
        update_history(scan_id, started_at, now_jst(), 0, "error")
        return

    missing = [k for k in ["market", "industry", "code", "name"] if k not in col_map]
    if missing:
        print("missing columns: " + str(missing))
        set_status("state",   "done")
        set_status("current", "失敗:列が見つかりません " + str(missing))
        update_history(scan_id, started_at, now_jst(), 0, "error")
        return

    jpx_df = df[df[col_map["market"]].astype(str).str.contains("プライム")].copy()
    print("prime rows: " + str(len(jpx_df)))

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

    print("industries count: " + str(len(all_industries)))

    if len(all_industries) == 0:
        print("ERROR: no industries found")
        set_status("state",   "done")
        set_status("current", "失敗:業種データが取得できませんでした")
        update_history(scan_id, started_at, now_jst(), 0, "error")
        return

    total       = len(all_industries) + 1
    all_results = []

    for idx, industry in enumerate(all_industries):
        print("[" + str(idx+1) + "/" + str(total) + "] " + industry)
        set_status("current",  "[" + str(idx+1) + "/" + str(total) + "] " + industry)
        set_status("progress", str(round((idx / total) * 100)))

        sector_df = non_shosha_df[non_shosha_df[col_map["industry"]] == industry]
        targets   = get_sector_targets(sector_df, col_map)
        print("  target: " + str(len(targets)) + "社 (大型株優先)")

        candidates = scan_sector(targets, industry, col_map, forced=False)
        if not candidates:
            print("  -> forced mode")
            candidates = scan_sector(targets, industry, col_map, forced=True)

        if candidates:
            top5 = sorted(candidates, key=lambda x: (x["score"], x["m_cap"]), reverse=True)[:5]
            now  = now_jst()
            for r in top5:
                all_results.append((
                    scan_id, now, r["industry"], r["code"], r["name"],
                    r["dy"], r["payout"], r["eq"], r["mcap"],
                    r["judge"], r["stars"], r["note"], r["score"]
                ))
            save_results(all_results, scan_id)

    print("scanning shosha...")
    set_status("current", "商社(総合商社)")
    shosha_targets = get_sector_targets(shosha_df, col_map)
    shosha_cand = scan_sector(shosha_targets, "商社", col_map, forced=False)
    if not shosha_cand:
        shosha_cand = scan_sector(shosha_targets, "商社", col_map, forced=True)
    if shosha_cand:
        now = now_jst()
        for r in sorted(shosha_cand, key=lambda x: (x["score"], x["m_cap"]), reverse=True):
            all_results.append((
                scan_id, now, r["industry"], r["code"], r["name"],
                r["dy"], r["payout"], r["eq"], r["mcap"],
                r["judge"], r["stars"], r["note"], r["score"]
            ))

    save_results(all_results, scan_id)
    finished_at = now_jst()
    set_status("state",    "done")
    set_status("progress", "100")
    set_status("finished", finished_at)
    set_status("current",  "完了(" + str(len(all_results)) + "銘柄)")
    update_history(scan_id, started_at, finished_at, len(all_results), "done")
    print("=== done: " + str(len(all_results)) + " ===")

if __name__ == "__main__":
    main()
