"""yfinanceラッパー — 株価データ取得"""
import logging
from datetime import datetime
from typing import Optional

import pandas as pd
import yfinance as yf

from sr_data import cache, db

_FX_FALLBACK = {"USDJPY": 150.0, "JPYUSD": 1 / 150.0}


def get_fx_rate(from_ccy: str = "USD", to_ccy: str = "JPY") -> float:
    """為替レートを取得（USD→JPY など）。取得失敗時はフォールバック値を返す"""
    key = f"{from_ccy}{to_ccy}"
    cached = cache.get(f"fx:{key}")
    if cached is not None:
        return cached
    try:
        ticker = f"{from_ccy}{to_ccy}=X"
        rate = float(yf.Ticker(ticker).fast_info.last_price or 0)
        if rate <= 0:
            raise ValueError("invalid rate")
        cache.set(f"fx:{key}", rate, ttl=300)  # 5分キャッシュ
        return rate
    except Exception:
        fallback = _FX_FALLBACK.get(key, 1.0)
        logger.warning(f"[fx] {key} 取得失敗 → フォールバック {fallback}")
        return fallback


def to_jpy(usd_price: float) -> float:
    """USD価格をJPYに換算"""
    return usd_price * get_fx_rate("USD", "JPY")


def is_us_ticker(ticker: str) -> bool:
    return not ticker.endswith(".T")

logger = logging.getLogger(__name__)


def get_quote(ticker: str) -> Optional[dict]:
    """リアルタイム気配値を返す（キャッシュ付き）"""
    cached = cache.get(f"quote:{ticker}")
    if cached is not None:
        return cached

    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        price = float(info.last_price or 0)
        prev_close = float(info.previous_close or price)
        change = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0.0

        result = {
            "ticker": ticker,
            "price": price,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "volume": int(info.three_month_average_volume or 0),
            "currency": getattr(info, "currency", "JPY"),
            "fetched_at": datetime.now().isoformat(),
        }
        cache.set(f"quote:{ticker}", result)
        return result
    except Exception as e:
        logger.warning(f"[fetcher] {ticker} get_quote failed: {e}")
        return None


def get_batch_quotes(tickers: list[str]) -> dict[str, dict]:
    """複数銘柄をまとめて取得（効率化）"""
    results = {}
    missing = [t for t in tickers if cache.get(f"quote:{t}") is None]

    if missing:
        try:
            data = yf.download(missing, period="2d", interval="1d",
                               auto_adjust=True, progress=False, threads=True)
            close = data["Close"] if isinstance(data.columns, pd.MultiIndex) else data[["Close"]]
            volume = data["Volume"] if isinstance(data.columns, pd.MultiIndex) else data[["Volume"]]

            for ticker in missing:
                try:
                    col = ticker if ticker in close.columns else close.columns[0]
                    prices = close[col].dropna()
                    vols = volume[col].dropna()
                    if len(prices) < 1:
                        continue
                    price = float(prices.iloc[-1])
                    prev = float(prices.iloc[-2]) if len(prices) >= 2 else price
                    change = price - prev
                    change_pct = (change / prev * 100) if prev else 0.0
                    vol = int(vols.iloc[-1]) if len(vols) >= 1 else 0
                    q = {
                        "ticker": ticker,
                        "price": price,
                        "prev_close": prev,
                        "change": change,
                        "change_pct": change_pct,
                        "volume": vol,
                        "currency": "JPY" if ticker.endswith(".T") else "USD",
                        "fetched_at": datetime.now().isoformat(),
                    }
                    cache.set(f"quote:{ticker}", q)
                    results[ticker] = q
                except Exception as e:
                    logger.warning(f"[fetcher] batch parse error {ticker}: {e}")
        except Exception as e:
            logger.warning(f"[fetcher] batch download failed: {e}")
            for t in missing:
                q = get_quote(t)
                if q:
                    results[t] = q

    for t in tickers:
        cached = cache.get(f"quote:{t}")
        if cached and t not in results:
            results[t] = cached

    return results


def get_history(ticker: str, period: str = "6mo", interval: str = "1d",
                save_to_db: bool = True) -> pd.DataFrame:
    """OHLCV履歴を取得してDBに保存"""
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         auto_adjust=True, progress=False)
        if df.empty:
            return pd.DataFrame()

        # MultiIndex列をフラット化
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]

        df = df.rename(columns={"adj close": "close"})
        df = df[["open", "high", "low", "close", "volume"]].dropna()
        df.index = pd.to_datetime(df.index)

        if save_to_db:
            rows = [
                {
                    "ticker": ticker,
                    "ts": ts.isoformat(),
                    "interval": interval,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                }
                for ts, row in df.iterrows()
            ]
            db.upsert_ohlcv(rows)

        return df
    except Exception as e:
        logger.warning(f"[fetcher] {ticker} get_history failed: {e}")
        return pd.DataFrame()


def get_history_from_db(ticker: str, interval: str = "1d", limit: int = 200) -> pd.DataFrame:
    """DBキャッシュからOHLCVを取得"""
    rows = db.get_ohlcv(ticker, interval=interval, limit=limit)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()
    return df[["open", "high", "low", "close", "volume"]]
