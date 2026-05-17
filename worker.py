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
            score INTEGER
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
         equity_pct, mcap_oku, judge, stars, note, score, yutai, div_history, eps_str, div_trend)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        elif "規模コード" in c:
            col_map["size_code"] = col
    return df, col_map

def get_sector_targets(sector_df, col_map):
    df = sector_df.copy()

    if "size_code" in col_map and col_map["size_code"] in df.columns:
        df["_sort"] = pd.to_numeric(df[col_map["size_code"]], errors="coerce").fillna(99)
        df = df.sort_values("_sort")
        return df.head(30)

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
        return df.head(30)

    return df.head(30)

def check_dividend_history(ticker):
    try:
        divs = ticker.dividends
        if divs is None or len(divs) == 0:
            return 0, 0, False, "配当履歴なし", []

        divs.index = divs.index.tz_localize(None) if divs.index.tzinfo else divs.index
        cutoff = pd.Timestamp.now() - pd.DateOffset(years=10)
        divs   = divs[divs.index >= cutoff]

        if len(divs) == 0:
            return 0, 0, False, "配当履歴なし", []

        annual = divs.resample("YE").sum()
        annual = annual[annual > 0]
        # 現在年は年途中で不完全（直近の急落に見える）ため除外
        current_year = pd.Timestamp.now().year
        annual = annual[annual.index.year < current_year]

        def _annual_data(a):
            return [[int(a.index[i].year), round(float(a.iloc[i]), 1)] for i in range(len(a))]

        if len(annual) < 2:
            return 0, len(annual), False, "配当履歴" + str(len(annual)) + "年分", _annual_data(annual), 0, False, []

        years_checked = len(annual)
        cut_count     = 0
        cut_years     = []

        for i in range(1, len(annual)):
            prev = annual.iloc[i - 1]
            curr = annual.iloc[i]
            if curr < prev * 0.95:
                cut_count += 1
                cut_years.append(str(annual.index[i].year))

        is_increasing = False
        if len(annual) >= 3:
            # 前半vs後半の平均比較（直近の下落を無視しないため固定3年比較をやめる）
            mid       = len(annual) // 2
            early_avg = annual.iloc[:mid].mean()
            late_avg  = annual.iloc[mid:].mean()
            trend_up  = early_avg > 0 and late_avg > early_avg * 1.05
            # 直近年が前年から5%超下落していれば増配傾向とみなさない
            recent_ok = annual.iloc[-1] >= annual.iloc[-2] * 0.95
            if trend_up and recent_ok:
                is_increasing = True
        elif len(annual) >= 2:
            if annual.iloc[-1] > annual.iloc[0] * 1.05:
                is_increasing = True

        # 直近連続増配年数（前年比で増えている年が何年続いているか）
        consecutive_increase = 0
        for i in range(len(annual) - 1, 0, -1):
            if annual.iloc[i] > annual.iloc[i - 1]:
                consecutive_increase += 1
            else:
                break

        # 直前の完了年が減配だったか
        last_year_cut = len(annual) >= 2 and annual.iloc[-1] < annual.iloc[-2] * 0.95

        detail = "配当" + str(years_checked) + "年確認"
        if is_increasing:
            detail += "/増加傾向"
        if cut_years:
            detail += "/減配:" + ",".join(cut_years)

        return cut_count, years_checked, is_increasing, detail, _annual_data(annual), consecutive_increase, last_year_cut, cut_years

    except Exception as e:
        return 0, 0, False, "配当履歴取得失敗", [], 0, False, []

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

def get_payout_ratio(info, ticker):
    f_eps = info.get("forwardEps")
    t_eps = info.get("trailingEps")

    # trailingEps（実績値）を優先。forwardEpsを先に使うと
    # 直近赤字等の銘柄で配当性向が低く見えてしまう。
    if t_eps is not None and t_eps > 0:
        eps = t_eps
    elif f_eps is not None and f_eps > 0:
        eps = f_eps
    else:
        return 0

    if eps is None or eps <= 0:
        return 0

    div_rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate") or 0

    if div_rate == 0:
        try:
            divs = ticker.dividends
            if divs is not None and len(divs) > 0:
                divs.index = divs.index.tz_localize(None) if divs.index.tzinfo else divs.index
                cutoff = pd.Timestamp.now() - pd.DateOffset(years=1)
                div_rate = divs[divs.index >= cutoff].sum()
        except:
            pass

    if div_rate <= 0:
        return 0

    payout = round(div_rate / eps * 100, 1)

    if payout <= 0 or payout > 1000:
        return 0

    return payout

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

def get_equity_ratio(info, ticker, is_finance):
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

    return 0.0

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

def get_dividend_yield(info, ticker, price):
    """利回りを計算。dividendRateが取れない場合はdividendsの直近1年合計を使用"""
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


def get_yutai(code4):
    """
    株主優待情報を取得。kabutan.jp → minkabu.jp の順で試みる。
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
        f"https://kabutan.jp/stock/yutai?code={code4}",
        f"https://minkabu.jp/stock/{code4}/yutai",
    ]
    for url in urls:
        try:
            sess = get_robust_session()
            time.sleep(0.8 + random.uniform(0.2, 0.6))
            resp = sess.get(url, timeout=15)
            print(f"    yutai {code4}: HTTP {resp.status_code} ({url})")
            if resp.status_code != 200:
                continue

            html = resp.text

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

    dy = get_dividend_yield(info, ticker, price)

    if dy > 30:
        print("    reason: abnormal yield=" + str(dy))
        return {"excluded": True, "reason": "利回り異常値=" + str(dy) + "%", "dy": dy}

    if not forced and dy < MIN_YIELD:
        print("    reason: low yield=" + str(dy))
        return {"excluded": True, "reason": "利回り" + str(dy) + "%<" + str(MIN_YIELD) + "%", "dy": dy}

    score   = 5
    reasons = []

    if forced and dy < MIN_YIELD:
        score -= 1
        reasons.append("利回り" + str(dy) + "%(低め)")

    cut_count, years_checked, is_increasing, div_detail, annual_list, consecutive_increase, last_year_cut, cut_years = check_dividend_history(ticker)
    div_history = make_div_history_str(cut_count, years_checked, is_increasing)
    div_trend   = json.dumps(annual_list) if annual_list else "[]"

    _cut_yr_str = "(" + "・".join(cut_years) + "年)" if cut_years else ""
    if cut_count >= 2:
        if is_increasing:
            score -= 2
            note = "減配歴" + str(cut_count) + "回" + _cut_yr_str + "(増配傾向継続)"
            if consecutive_increase > 0:
                note += "/連続増配" + str(consecutive_increase) + "年"
            reasons.append(note)
            print("    div cut " + str(cut_count) + " but increasing trend: include with penalty")
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

    # 当年配当確定分：前年同期比チェック（減配確定は除外、前年減配+今年増配未確認も除外）
    curr_year_increase = False
    try:
        rd = ticker.dividends
        if rd is not None and len(rd) > 0:
            rd = rd.copy()
            rd.index = rd.index.tz_localize(None) if rd.index.tzinfo else rd.index
            now    = pd.Timestamp.now()
            cur_yr = now.year
            curr_sum = rd[rd.index.year == cur_yr].sum()
            if curr_sum > 0:
                prev_sum = rd[
                    (rd.index >= pd.Timestamp(cur_yr - 1, 1, 1)) &
                    (rd.index <= pd.Timestamp(cur_yr - 1, now.month, now.day))
                ].sum()
                if prev_sum > 0:
                    chg = (curr_sum - prev_sum) / prev_sum * 100
                    if chg >= 5:
                        curr_year_increase = True
                        reasons.append(str(cur_yr) + "年増配確定(" + str(round(curr_sum, 1)) + "円/前年同期比+" + str(round(chg, 1)) + "%)")
                    elif chg <= -5:
                        print("    reason: " + str(cur_yr) + "年減配確定")
                        return {"excluded": True, "reason": str(cur_yr) + "年減配確定(" + str(round(curr_sum, 1)) + "円/前年同期比" + str(round(chg, 1)) + "%)", "dy": dy}
                    elif chg < 0:
                        # -5%未満の減少：除外はしないが備考に記載
                        reasons.append(str(cur_yr) + "年減配予定(" + str(round(curr_sum, 1)) + "円/前年同期比" + str(round(chg, 1)) + "%)")
                    else:
                        reasons.append(str(cur_yr) + "年配当確定(" + str(round(curr_sum, 1)) + "円)")
                else:
                    reasons.append(str(cur_yr) + "年配当確定(" + str(round(curr_sum, 1)) + "円)")
    except Exception:
        pass

    if last_year_cut and not curr_year_increase:
        prev_yr = pd.Timestamp.now().year - 1
        print("    reason: " + str(prev_yr) + "年減配・今年増配未確認")
        return {"excluded": True, "reason": str(prev_yr) + "年減配・今年増配未確認", "dy": dy}

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
    eps_str = make_eps_str(t_eps, f_eps)

    payout = get_payout_ratio(info, ticker)
    if payout == 0:
        print("    reason: payout cannot be calculated")
        return {"excluded": True, "reason": "配当性向計算不可(EPS不明)", "dy": dy}

    if payout > 70:
        ok, note = check_payout_recovery(info)
        if not ok:
            print("    reason: payout=" + str(round(payout)) + "% no recovery")
            return {"excluded": True, "reason": "配当性向" + str(round(payout)) + "%・回復見込みなし", "dy": dy}
        score -= 1
        reasons.append("配当性向" + str(round(payout)) + "%(一時的)")
        reasons.append(note)
    elif payout < 30:
        score -= 1
        reasons.append("配当性向" + str(round(payout)) + "%(低)")

    is_finance = any(x in industry for x in ["銀行", "保険", "証券", "その他金融"])
    eq_ratio   = get_equity_ratio(info, ticker, is_finance)
    if eq_ratio > 0 and eq_ratio < 40 and not is_finance:
        score -= 1
        reasons.append("自己資本" + str(eq_ratio) + "%")

    m_cap = info.get("marketCap") or 0
    star  = max(1, score)
    judge = "〇" if star >= 4 else ("△" if star >= 2 else "×")
    code4 = symbol.replace(".T", "")
    yutai = get_yutai(code4)

    return {
        "excluded":    False,
        "dy":          dy,
        "payout":      round(payout, 1),
        "eq":          eq_ratio,
        "mcap":        round(m_cap / 100000000) if m_cap else 0,
        "judge":       judge,
        "stars":       "★" * star + "☆" * (5 - star),
        "note":        " / ".join(reasons) if reasons else "良好(指標クリア)",
        "score":       star,
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
        set_status("state",   "done")
        update_history(scan_id, started_at, now_jst(), 0, "error")
        return

    missing = [k for k in ["market", "industry", "code", "name"] if k not in col_map]
    if missing:
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
                    r.get("div_history", "-"), r.get("eps_str", "-"), r.get("div_trend", "[]")
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
                r.get("div_history", "-"), r.get("eps_str", "-"), r.get("div_trend", "[]")
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
