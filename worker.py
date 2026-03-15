# worker.py
# 実行方法: python worker.py
# ブラウザを閉じても動き続ける

import pandas as pd
import yfinance as yf
import sqlite3
import time
import random
import json
from datetime import datetime

SOGO_SHOSHA_CODES = {8058, 8031, 8001, 8053, 8002, 8015, 2768}
MIN_YIELD    = 3.0
INFO_WAIT    = 1.0
MAX_RETRY    = 3
SECTOR_TOP   = 20
DB_PATH      = "results.db"

# ── DB初期化 ────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    c.execute("""
        CREATE TABLE IF NOT EXISTS scan_status (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()

def set_status(key, value):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO scan_status VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def save_results(rows):
    if not rows:
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM scan_results")   # 前回結果をクリア
    conn.executemany("""
        INSERT INTO scan_results
        (scanned_at, industry, code, name, yield_pct, payout_pct,
         equity_pct, mcap_oku, judge, stars, note, score)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()
    conn.close()

# ── JPXデータ取得 ───────────────────────────────────────
def fetch_jpx_prime():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    df = pd.read_excel(url, header=0)
    col_map = {}
    for col in df.columns:
        c = str(col).strip()
        if '市場' in c or '商品区分' in c:    col_map['market']   = col
        elif '33業種' in c or '業種区分' in c: col_map['industry'] = col
        elif 'コード' in c:                    col_map['code']     = col
        elif '銘柄' in c or '名称' in c:       col_map['name']     = col
        elif '規模' in c:                      col_map['size']     = col
    return df, col_map

# ── 分析ロジック ────────────────────────────────────────
def check_payout_recovery(info):
    t_eps = info.get('trailingEps')
    f_eps = info.get('forwardEps')
    if t_eps and f_eps and t_eps != 0 and f_eps > t_eps:
        pct = round((f_eps - t_eps) / abs(t_eps) * 100, 1)
        return True, f"業績回復見込み(予EPS+{pct}%)"
    rec = (info.get('recommendationKey') or '').lower()
    if rec in ('buy', 'strong_buy'):
        return True, "業績回復見込み(アナリスト買い推奨)"
    return False, ""

def fetch_info_retry(symbol):
    for attempt in range(MAX_RETRY):
        try:
            time.sleep(INFO_WAIT + random.uniform(0, 0.5))
            ticker = yf.Ticker(symbol)
            info   = ticker.info
            if not info or len(info) < 5:
                return None, ticker
            return info, ticker
        except Exception as e:
            msg = str(e)
            if '429' in msg or 'Too Many' in msg:
                wait = (2 ** attempt) * 10
                print(f"  レート制限 {symbol}、{wait}秒待機...")
                time.sleep(wait)
            else:
                print(f"  例外 {symbol}: {msg[:60]}")
                return None, None
    return None, None

def analyze(symbol, industry, forced=False):
    info, ticker = fetch_info_retry(symbol)
    if info is None:
        return None

    price    = info.get('currentPrice') or info.get('previousClose') or 0
    if price == 0:
        return None

    div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate') or 0
    dy = round(div_rate / price * 100, 2)

    if not forced and dy < MIN_YIELD:
        return None

    score   = 5
    reasons = []

    if forced and dy < MIN_YIELD:
        score -= 1
        reasons.append(f"利回り{dy}%（低め）")

    # 成長性
    rev_g = info.get('revenueGrowth')
    ear_g = info.get('earningsGrowth')
    if rev_g is not None:
        if rev_g < 0:
            score -= 1
            reasons.append(f"売上減({round(rev_g*100,1)}%)")
    elif ear_g is not None:
        if ear_g < 0:
            score -= 1
            reasons.append(f"利益減({round(ear_g*100,1)}%)")
    else:
        reasons.append("成長データ不明")

    # 配当性向
    payout = info.get('payoutRatio') or 0
    if 0 < payout <= 1.0:
        payout *= 100
    if payout > 70:
        ok, note = check_payout_recovery(info)
        if not ok:
            return None
        score -= 1
        reasons.append(f"配当性向{round(payout)}%（一時的）")
        reasons.append(note)
    elif 0 < payout < 30:
        score -= 1
        reasons.append(f"配当性向{round(payout)}%（低）")
    elif payout == 0:
        reasons.append("性向データなし")

    # 自己資本比率
    is_finance = any(x in industry for x in ['銀行', '保険', '証券', 'その他金融'])
    eq_ratio   = 0.0
    dte = info.get('debtToEquity')
    if dte is not None and dte >= 0:
        eq_ratio = round(100 / (1 + dte / 100), 1)
        if not is_finance and eq_ratio < 40:
            score -= 1
            reasons.append(f"自己資本≈{eq_ratio}%")

    m_cap = info.get('marketCap') or 0
    star  = max(1, score)
    judge = "〇" if star >= 4 else ("△" if star >= 2 else "×")

    return {
        'dy': dy, 'payout': round(payout, 1), 'eq': eq_ratio,
        'mcap': round(m_cap / 1_0000_0000) if m_cap else 0,
        'judge': judge,
        'stars': "★" * star + "☆" * (5 - star),
        'note': " / ".join(reasons) if reasons else "良好（指標クリア）",
        'score': star, 'm_cap': m_cap,
    }

def scan_sector(rows, industry, col_map, forced=False):
    candidates = []
    for _, row in rows.head(SECTOR_TOP).iterrows():
symbol = f"{int(row[col_map['code']]):04d}.T"
        res = analyze(symbol, industry, forced)
        if res:
            res.update({
                'industry': industry,
                'code':     int(row[col_map['code']]),
                'name':     row[col_map['name']],
            })
            candidates.append(res)
    return candidates

# ── メイン実行 ─────────────────────────────────────────
def main():
    print(f"=== スキャン開始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    init_db()
    set_status('state',    'running')
    set_status('progress', '0')
    set_status('current',  '準備中')
    set_status('started',  datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    df, col_map = fetch_jpx_prime()

    jpx_df = df[df[col_map['market']].astype(str).str.contains('プライム')].copy()
jpx_df[col_map['code']] = (
    jpx_df[col_map['code']]
    .astype(str)
    .str.strip()
    .str.extract(r'(\d{4})', expand=False)  # 4桁の数字を抽出
)
jpx_df = jpx_df.dropna(subset=[col_map['code']])
jpx_df[col_map['code']] = jpx_df[col_map['code']].astype(int)
    if 'size' in col_map:
        size_order = {'大型株': 0, '中型株': 1, '小型株': 2}
        jpx_df['_rank'] = jpx_df[col_map['size']].map(size_order).fillna(3)
        jpx_df = jpx_df.sort_values(['_rank', col_map['code']])

    shosha_df     = jpx_df[jpx_df[col_map['code']].isin(SOGO_SHOSHA_CODES)]
    non_shosha_df = jpx_df[~jpx_df[col_map['code']].isin(SOGO_SHOSHA_CODES)]
    all_industries = sorted(non_shosha_df[col_map['industry']].dropna().unique())
    total = len(all_industries) + 1

    all_results = []

    for idx, industry in enumerate(all_industries):
        print(f"[{idx+1}/{total}] {industry}")
        set_status('current',  f"[{idx+1}/{total}] {industry}")
        set_status('progress', str(round((idx / total) * 100)))

        sector_df  = non_shosha_df[non_shosha_df[col_map['industry']] == industry]
        candidates = scan_sector(sector_df, industry, col_map, forced=False)

        if not candidates:
            print(f"  → 強制選出モード")
            candidates = scan_sector(sector_df, industry, col_map, forced=True)

        if candidates:
            top5 = sorted(candidates, key=lambda x: (x['score'], x['m_cap']), reverse=True)[:5]
            now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            for r in top5:
                all_results.append((
                    now, r['industry'], r['code'], r['name'],
                    r['dy'], r['payout'], r['eq'], r['mcap'],
                    r['judge'], r['stars'], r['note'], r['score']
                ))
            # 業種完了ごとに随時保存（途中経過も見られる）
            save_results(all_results)

    # 商社
    print("商社スキャン中...")
    set_status('current', '商社（総合商社）')
    shosha_cand = scan_sector(shosha_df, "商社", col_map, forced=False)
    if not shosha_cand:
        shosha_cand = scan_sector(shosha_df, "商社", col_map, forced=True)
    if shosha_cand:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for r in sorted(shosha_cand, key=lambda x: (x['score'], x['m_cap']), reverse=True):
            all_results.append((
                now, r['industry'], r['code'], r['name'],
                r['dy'], r['payout'], r['eq'], r['mcap'],
                r['judge'], r['stars'], r['note'], r['score']
            ))

    save_results(all_results)
    set_status('state',    'done')
    set_status('progress', '100')
    set_status('finished', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    set_status('current',  f"完了（{len(all_results)}銘柄）")
    print(f"=== 完了: {len(all_results)} 銘柄 ===")

if __name__ == '__main__':
    main()
