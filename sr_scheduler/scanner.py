"""定期スキャンジョブ（APScheduler）"""
import logging
from datetime import datetime

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler

from sr_analysis import alerts, indicators
from sr_analysis.anomaly import detect_volume_spike
from sr_data import db, fetcher
from sr_display import watchlist_view
from sr_simulation.paper_trader import PaperTrader

logger = logging.getLogger(__name__)

JST = pytz.timezone("Asia/Tokyo")
ET  = pytz.timezone("America/New_York")


def _is_market_open() -> bool:
    """日本株か米国株のいずれかの市場が開いていれば True"""
    now_jst = datetime.now(JST)
    now_et  = datetime.now(ET)

    # 土日はスキップ
    if now_jst.weekday() >= 5:
        return False

    # 東証: 9:00–15:30 JST
    tse_open  = now_jst.replace(hour=9,  minute=0,  second=0, microsecond=0)
    tse_close = now_jst.replace(hour=15, minute=30, second=0, microsecond=0)
    if tse_open <= now_jst <= tse_close:
        return True

    # NYSE/NASDAQ: 9:30–16:00 ET
    nyse_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    nyse_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    if nyse_open <= now_et <= nyse_close:
        return True

    return False


def run_scan(config: dict, trader: PaperTrader | None = None) -> dict:
    """1回分のスキャンを実行して結果を返す"""
    market_hours_only = config.get("market_hours_only", True)
    if market_hours_only and not _is_market_open():
        logger.info("[scanner] 市場時間外 — スキップ")
        return {}

    watchlist = db.get_watchlist()
    if not watchlist:
        logger.info("[scanner] ウォッチリストが空です")
        return {}

    tickers = [w["ticker"] for w in watchlist]
    quotes = fetcher.get_batch_quotes(tickers)
    rules = db.get_alert_rules()

    indicators_map = {}
    triggered_alerts = []

    for item in watchlist:
        ticker = item["ticker"]
        quote = quotes.get(ticker)
        if not quote:
            continue

        # 履歴取得・指標計算
        df = fetcher.get_history_from_db(ticker)
        if df.empty or len(df) < 26:
            df = fetcher.get_history(ticker)
        if df.empty or len(df) < 26:
            continue

        df = indicators.compute_all(df)

        rsi_val = float(df["rsi"].iloc[-1]) if "rsi" in df.columns and not df["rsi"].isna().all() else None
        macd_val = float(df["macd_hist"].iloc[-1]) if "macd_hist" in df.columns and not df["macd_hist"].isna().all() else None
        is_spike, spike_ratio = detect_volume_spike(df, threshold=config.get("alert_rules", [{}])[0].get("threshold", 2.5)
                                                    if config.get("alert_rules") else 2.5)

        indicators_map[ticker] = {
            "rsi": rsi_val,
            "macd_hist": macd_val,
            "volume_spike": is_spike,
            "spike_ratio": spike_ratio,
        }

        # アラート評価
        fired = alerts.evaluate(ticker, quote, df, rules)
        triggered_alerts.extend(fired)

    # ウォッチリスト表示
    watchlist_view.render(watchlist, quotes, indicators_map)

    # ペーパートレード自動売買
    if trader and config.get("paper_trading", {}).get("enabled", False):
        executed = trader.tick(tickers)
        if executed:
            for e in executed:
                logger.info(f"[paper] {e['action']} {e['ticker']} {e['shares']}株 @{e['price']:.2f} ({e['reason']})")

    return {
        "quotes": quotes,
        "indicators": indicators_map,
        "alerts": triggered_alerts,
    }


def start(config: dict):
    """定期スキャンを開始（ブロッキング）"""
    interval = config.get("scan_interval_minutes", 5)

    trader = None
    if config.get("paper_trading", {}).get("enabled", False):
        trader = PaperTrader(config)

    scheduler = BlockingScheduler(timezone=JST)
    scheduler.add_job(
        run_scan,
        "interval",
        minutes=interval,
        kwargs={"config": config, "trader": trader},
        id="scan_job",
        max_instances=1,
        coalesce=True,
    )

    from rich.console import Console
    Console().print(f"[bold green]定期スキャン開始[/] — {interval}分ごと  [dim](Ctrl+C で停止)[/]")
    # 初回即時実行
    run_scan(config, trader)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        Console().print("[yellow]スキャン停止[/]")
