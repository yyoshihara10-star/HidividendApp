# worker.py
import pandas as pd
import yfinance as yf
import sqlite3
import time
import random
import os
from datetime import datetime

SOGO_SHOSHA_CODES = {8058, 8031, 8001, 8053, 8002, 8015, 2768}
MIN_YIELD  = 3.0
INFO_WAIT  = 1.0
MAX_RETRY  = 3
SECTOR_TOP = 20
DB_PATH    = "results.db"

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
    conn.execute("DELETE FROM scan_results")
    conn.executemany("""
        INSERT INTO scan_results
        (scanned_at, industry, code, name, yield_pct, payout_pct,
         equity_pct, mcap_oku, judge, stars, note, score)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()
    conn.close()

def fetch_jpx_prime():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    df = pd.read_excel(url, header=0)
    col_map
