"""SQLite永続化レイヤー"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "stock_radar.db"


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            ticker   TEXT NOT NULL,
            ts       TEXT NOT NULL,
            interval TEXT NOT NULL,
            open     REAL NOT NULL,
            high     REAL NOT NULL,
            low      REAL NOT NULL,
            close    REAL NOT NULL,
            volume   INTEGER NOT NULL,
            PRIMARY KEY (ticker, ts, interval)
        );
        CREATE INDEX IF NOT EXISTS idx_ohlcv ON ohlcv(ticker, ts);

        CREATE TABLE IF NOT EXISTS watchlist (
            ticker       TEXT PRIMARY KEY,
            market       TEXT NOT NULL,
            display_name TEXT,
            added_at     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS alert_rules (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker    TEXT,
            rule_type TEXT NOT NULL,
            threshold REAL NOT NULL,
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT NOT NULL,
            rule_type       TEXT NOT NULL,
            triggered_value REAL NOT NULL,
            threshold       REAL NOT NULL,
            triggered_at    TEXT NOT NULL,
            acknowledged    INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS trades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            session_type TEXT NOT NULL,
            ticker       TEXT NOT NULL,
            side         TEXT NOT NULL,
            shares       REAL NOT NULL,
            price        REAL NOT NULL,
            executed_at  TEXT NOT NULL,
            strategy     TEXT
        );

        CREATE TABLE IF NOT EXISTS portfolio_state (
            session_id TEXT NOT NULL,
            ticker     TEXT NOT NULL,
            shares     REAL NOT NULL,
            avg_cost   REAL NOT NULL,
            peak_price REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (session_id, ticker)
        );

        CREATE TABLE IF NOT EXISTS portfolio_cash (
            session_id TEXT PRIMARY KEY,
            cash       REAL NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)


# ─── OHLCV ────────────────────────────────────────────────

def upsert_ohlcv(rows: list[dict]):
    """OHLCVデータをバルクUPSERT"""
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO ohlcv
               (ticker, ts, interval, open, high, low, close, volume)
               VALUES (:ticker, :ts, :interval, :open, :high, :low, :close, :volume)""",
            rows,
        )


def get_ohlcv(ticker: str, interval: str = "1d", limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM ohlcv WHERE ticker=? AND interval=?
               ORDER BY ts DESC LIMIT ?""",
            (ticker, interval, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ─── Watchlist ────────────────────────────────────────────

def add_to_watchlist(ticker: str, market: str, display_name: str = ""):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO watchlist (ticker, market, display_name, added_at)
               VALUES (?, ?, ?, ?)""",
            (ticker, market, display_name, datetime.now().isoformat()),
        )


def remove_from_watchlist(ticker: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM watchlist WHERE ticker=?", (ticker,))


def get_watchlist() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM watchlist ORDER BY market, ticker").fetchall()
    return [dict(r) for r in rows]


# ─── Alert rules ─────────────────────────────────────────

def upsert_alert_rule(ticker: Optional[str], rule_type: str, threshold: float):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO alert_rules (ticker, rule_type, threshold) VALUES (?, ?, ?)""",
            (ticker, rule_type, threshold),
        )


def get_alert_rules(ticker: Optional[str] = None) -> list[dict]:
    with get_conn() as conn:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM alert_rules WHERE (ticker=? OR ticker IS NULL) AND is_active=1",
                (ticker,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM alert_rules WHERE is_active=1"
            ).fetchall()
    return [dict(r) for r in rows]


# ─── Alerts ──────────────────────────────────────────────

def save_alert(ticker: str, rule_type: str, triggered_value: float, threshold: float):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO alerts (ticker, rule_type, triggered_value, threshold, triggered_at)
               VALUES (?, ?, ?, ?, ?)""",
            (ticker, rule_type, triggered_value, threshold, datetime.now().isoformat()),
        )


def get_alerts(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY triggered_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Portfolio ────────────────────────────────────────────

def get_cash(session_id: str, initial_cash: float = 1_000_000.0) -> float:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT cash FROM portfolio_cash WHERE session_id=?", (session_id,)
        ).fetchone()
    if row is None:
        set_cash(session_id, initial_cash)
        return initial_cash
    return row["cash"]


def set_cash(session_id: str, cash: float):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO portfolio_cash (session_id, cash, updated_at)
               VALUES (?, ?, ?)""",
            (session_id, cash, datetime.now().isoformat()),
        )


def get_positions(session_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM portfolio_state WHERE session_id=? AND shares > 0",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_position(session_id: str, ticker: str, shares: float, avg_cost: float, peak_price: float):
    with get_conn() as conn:
        if shares <= 0:
            conn.execute(
                "DELETE FROM portfolio_state WHERE session_id=? AND ticker=?",
                (session_id, ticker),
            )
        else:
            conn.execute(
                """INSERT OR REPLACE INTO portfolio_state
                   (session_id, ticker, shares, avg_cost, peak_price, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, ticker, shares, avg_cost, peak_price, datetime.now().isoformat()),
            )


def save_trade(session_id: str, session_type: str, ticker: str, side: str,
               shares: float, price: float, strategy: str = ""):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO trades
               (session_id, session_type, ticker, side, shares, price, executed_at, strategy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, session_type, ticker, side, shares, price,
             datetime.now().isoformat(), strategy),
        )


def get_trades(session_id: str, session_type: str = "paper", limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM trades WHERE session_id=? AND session_type=?
               ORDER BY executed_at DESC LIMIT ?""",
            (session_id, session_type, limit),
        ).fetchall()
    return [dict(r) for r in rows]
