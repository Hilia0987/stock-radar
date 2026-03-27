"""
stock_radar — 株式監視 & 仮想取引シミュレーション CLI
"""
import logging
import traceback
from pathlib import Path

import click
import yaml
from sr_display import shared_console as console
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

CONFIG_PATH = Path(__file__).parent / "configs" / "config.yaml"


def _load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ensure_db():
    from sr_data.db import init_db
    init_db()


# ─────────────────────────────────────────────
@click.group()
def cli():
    """株式監視 & 仮想取引シミュレーション「StockRadar」"""
    _ensure_db()


# ─── ウォッチリスト管理 ───────────────────────

@cli.command()
@click.argument("ticker")
@click.option("--market", "-m", type=click.Choice(["JP", "US"]), required=True,
              help="市場: JP（東証）/ US（NYSE/NASDAQ）")
@click.option("--name", "-n", default="", help="表示名（省略可）")
def add(ticker, market, name):
    """ウォッチリストに銘柄を追加する"""
    from sr_data.db import add_to_watchlist
    ticker = ticker.upper()
    add_to_watchlist(ticker, market, name)
    console.print(f"[green]追加:[/] {ticker} ({market}) {name}")


@cli.command()
@click.argument("ticker")
def remove(ticker):
    """ウォッチリストから銘柄を削除する"""
    from sr_data.db import remove_from_watchlist
    remove_from_watchlist(ticker.upper())
    console.print(f"[yellow]削除:[/] {ticker.upper()}")


@cli.command("list")
def list_cmd():
    """ウォッチリストと現在の株価をテーブル表示する"""
    from sr_data import db, fetcher
    from sr_analysis import indicators
    from sr_analysis.anomaly import detect_volume_spike
    from sr_display import watchlist_view

    watchlist = db.get_watchlist()
    if not watchlist:
        console.print("[dim]ウォッチリストが空です。'add' コマンドで銘柄を追加してください。[/]")
        return

    tickers = [w["ticker"] for w in watchlist]
    with console.status("株価を取得中..."):
        quotes = fetcher.get_batch_quotes(tickers)

    indicators_map = {}
    for item in watchlist:
        ticker = item["ticker"]
        df = fetcher.get_history_from_db(ticker)
        if df.empty or len(df) < 26:
            df = fetcher.get_history(ticker)
        if df.empty or len(df) < 26:
            continue
        df = indicators.compute_all(df)
        rsi = float(df["rsi"].iloc[-1]) if "rsi" in df.columns and not df["rsi"].isna().all() else None
        macd = float(df["macd_hist"].iloc[-1]) if "macd_hist" in df.columns and not df["macd_hist"].isna().all() else None
        is_spike, _ = detect_volume_spike(df)
        indicators_map[ticker] = {"rsi": rsi, "macd_hist": macd, "volume_spike": is_spike}

    watchlist_view.render(watchlist, quotes, indicators_map)


# ─── スキャン / 監視 ──────────────────────────

@cli.command()
def scan():
    """ウォッチリストを即時スキャン（1回）"""
    from sr_scheduler.scanner import run_scan
    config = _load_config()
    run_scan(config)


@cli.command()
def monitor():
    """定期スキャンを開始（config.yaml の間隔で繰り返す）"""
    from sr_scheduler.scanner import start
    config = _load_config()
    start(config)


# ─── チャート ────────────────────────────────

@cli.command()
@click.argument("ticker")
@click.option("--period", "-p", default="3mo",
              help="取得期間: 1mo / 3mo / 6mo / 1y (デフォルト: 3mo)")
def chart(ticker, period):
    """ターミナルにローソク足チャートを表示する"""
    from sr_data import fetcher
    from sr_analysis import indicators
    from sr_display import chart_view

    ticker = ticker.upper()
    with console.status(f"{ticker} データ取得中..."):
        df = fetcher.get_history(ticker, period=period)
    if df.empty:
        console.print(f"[red]データ取得失敗: {ticker}[/]")
        return
    df = indicators.compute_all(df)
    chart_view.render(ticker, df, period)


# ─── ポートフォリオ ───────────────────────────

@cli.command()
def portfolio():
    """仮想ポートフォリオの損益サマリを表示する"""
    from sr_data import db, fetcher
    from sr_simulation.portfolio import Portfolio
    from sr_display import portfolio_view

    config = _load_config()
    pt_cfg = config.get("paper_trading", {})
    session_id = pt_cfg.get("session_id", "default")
    initial_cash = pt_cfg.get("initial_cash", 1_000_000)

    p = Portfolio(session_id=session_id, initial_cash=initial_cash)
    tickers = list(p.positions.keys())
    if not tickers:
        quotes = {}
    else:
        with console.status("株価を取得中..."):
            quotes = fetcher.get_batch_quotes(tickers)

    portfolio_view.render(p, quotes)

    trades = db.get_trades(session_id, session_type="paper", limit=20)
    if trades:
        portfolio_view.render_trades(trades)


# ─── 手動売買 ─────────────────────────────────

@cli.group()
def trade():
    """手動ペーパートレード（buy / sell）"""
    pass


@trade.command()
@click.argument("ticker")
@click.argument("shares", type=float)
@click.option("--price", "-p", type=float, default=None,
              help="指定価格（省略時は現在値）")
def buy(ticker, shares, price):
    """銘柄を仮想購入する"""
    from sr_data import fetcher
    from sr_simulation.portfolio import Portfolio

    config = _load_config()
    pt_cfg = config.get("paper_trading", {})
    p = Portfolio(pt_cfg.get("session_id", "default"), pt_cfg.get("initial_cash", 1_000_000))
    ticker = ticker.upper()

    if price is None:
        q = fetcher.get_quote(ticker)
        if not q or q["price"] == 0:
            console.print(f"[red]価格取得失敗: {ticker}[/]")
            return
        price = q["price"]

    ok = p.buy(ticker, shares, price, strategy="manual")
    if ok:
        cur = "JPY" if ticker.endswith(".T") else "USD"
        console.print(f"[green]BUY[/] {ticker} {shares:.0f}株 @ {cur}{price:,.2f}")
    else:
        console.print("[red]購入失敗（資金不足）[/]")


@trade.command()
@click.argument("ticker")
@click.argument("shares", type=float)
@click.option("--price", "-p", type=float, default=None,
              help="指定価格（省略時は現在値）")
def sell(ticker, shares, price):
    """銘柄を仮想売却する"""
    from sr_data import fetcher
    from sr_simulation.portfolio import Portfolio

    config = _load_config()
    pt_cfg = config.get("paper_trading", {})
    p = Portfolio(pt_cfg.get("session_id", "default"), pt_cfg.get("initial_cash", 1_000_000))
    ticker = ticker.upper()

    if price is None:
        q = fetcher.get_quote(ticker)
        if not q or q["price"] == 0:
            console.print(f"[red]価格取得失敗: {ticker}[/]")
            return
        price = q["price"]

    ok = p.sell(ticker, shares, price, strategy="manual")
    if ok:
        cur = "JPY" if ticker.endswith(".T") else "USD"
        console.print(f"[red]SELL[/] {ticker} {shares:.0f}株 @ {cur}{price:,.2f}")
    else:
        console.print("[red]売却失敗（保有不足）[/]")


# ─── バックテスト ─────────────────────────────

@cli.command()
@click.argument("ticker")
@click.option("--strategy", "-s", default="composite",
              type=click.Choice(["ma_cross", "rsi", "composite"]),
              help="戦略 (デフォルト: composite)")
@click.option("--start", default="2024-01-01", help="開始日 YYYY-MM-DD")
@click.option("--end", default="", help="終了日 YYYY-MM-DD（省略時は今日）")
@click.option("--cash", default=1_000_000, type=float, help="初期資金（円）")
def backtest(ticker, strategy, start, end, cash):
    """過去データで戦略を検証する"""
    from sr_simulation import backtester
    from rich.table import Table
    from rich.panel import Panel
    from sr_display.chart_view import render as chart_render

    ticker = ticker.upper()
    console.print(f"[cyan]バックテスト実行中:[/] {ticker} / {strategy} / {start}～{end or '今日'}")

    with console.status("データ取得・計算中..."):
        result = backtester.run(ticker, strategy, start, end, cash)

    ret_color = "green" if result.total_return_pct >= 0 else "red"
    summary = (
        f"[bold]銘柄[/]     {result.ticker}\n"
        f"[bold]戦略[/]     {result.strategy_name}\n"
        f"[bold]初期資金[/] JPY{result.initial_cash:,.0f}\n"
        f"[bold]最終資産[/] JPY{result.final_equity:,.0f}\n"
        f"[bold]総リターン[/] [{ret_color}]{'+' if result.total_return_pct>=0 else ''}"
        f"{result.total_return_pct:.2f}%[/]\n"
        f"[bold]最大DD  [/] [red]{result.max_drawdown_pct:.2f}%[/]\n"
        f"[bold]シャープ[/] {result.sharpe_ratio:.3f}\n"
        f"[bold]取引数  [/] {result.num_trades}\n"
        f"[bold]勝率    [/] {result.win_rate_pct:.1f}%"
    )
    console.print(Panel(summary, title="[bold cyan]Backtest Result[/]", border_style="cyan"))

    # エクイティカーブ（x軸は数値インデックスで表示）
    if not result.equity_curve.empty:
        import plotext as plt
        plt.clf()
        plt.theme("dark")
        plt.title(f"{ticker} equity curve ({strategy})")
        plt.ylabel("JPY")
        plt.xlabel("days")
        vals = result.equity_curve.tolist()
        plt.plot(list(range(len(vals))), vals, color="cyan", label="equity")
        plt.hline(result.initial_cash, color="white")
        plt.show()


# ─── アラート履歴 ─────────────────────────────

@cli.command()
@click.option("--limit", "-n", default=20, help="表示件数")
def alerts(limit):
    """発火済みアラート履歴を表示する"""
    from sr_data.db import get_alerts
    from rich.table import Table
    from rich import box

    rows = get_alerts(limit)
    if not rows:
        console.print("[dim]アラート履歴なし[/]")
        return

    table = Table(title="[bold]アラート履歴[/]", box=box.SIMPLE)
    table.add_column("日時", width=20)
    table.add_column("銘柄", width=10)
    table.add_column("種別", width=16)
    table.add_column("値", justify="right", width=8)
    table.add_column("閾値", justify="right", width=8)
    for r in rows:
        table.add_row(
            r["triggered_at"][:19], r["ticker"], r["rule_type"],
            f"{r['triggered_value']:.2f}", f"{r['threshold']:.2f}",
        )
    console.print(table)


if __name__ == "__main__":
    cli()
