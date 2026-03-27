"""ポートフォリオ損益表示（rich）"""
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich import box
from rich.text import Text
from sr_display import shared_console as console


def render(portfolio, quotes: dict[str, dict]):
    positions = portfolio.positions
    cash = portfolio.cash
    initial = portfolio.initial_cash

    prices = {t: q["price"] for t, q in quotes.items() if q}
    total = portfolio.total_equity(prices)
    pnl = portfolio.unrealized_pnl(prices)
    total_return = (total - initial) / initial * 100 if initial else 0.0

    # ── サマリパネル ──
    pnl_color = "green" if pnl >= 0 else "red"
    ret_color = "green" if total_return >= 0 else "red"

    summary = (
        f"[bold]総資産[/]  [cyan]{_fmt(total)}[/]\n"
        f"[bold]現金  [/]  {_fmt(cash)}  "
        f"([dim]{cash/total*100:.1f}%[/])\n"
        f"[bold]含み損益[/] [{pnl_color}]{'+' if pnl>=0 else ''}{_fmt(pnl)}  "
        f"({'+' if total_return>=0 else ''}{total_return:.2f}%)[/]"
    )
    console.print(Panel(summary, title="[bold cyan]Portfolio[/]", border_style="cyan"))

    if not positions:
        console.print("[dim]  保有銘柄なし[/]")
        return

    # ── 保有銘柄テーブル ──
    table = Table(box=box.SIMPLE_HEAVY, header_style="bold white")
    table.add_column("Ticker", width=10)
    table.add_column("保有数", justify="right", width=8)
    table.add_column("平均取得", justify="right", width=12)
    table.add_column("現在値", justify="right", width=12)
    table.add_column("損益額", justify="right", width=12)
    table.add_column("損益率", justify="right", width=8)
    table.add_column("損切ライン", justify="right", width=12)

    for ticker, pos in positions.items():
        price = prices.get(ticker, pos.avg_cost)
        pnl_pos = pos.shares * (price - pos.avg_cost)
        pnl_pct = (price - pos.avg_cost) / pos.avg_cost * 100 if pos.avg_cost else 0
        stop = pos.avg_cost * (1 - 0.07)
        cur = "JPY" if ticker.endswith(".T") else "USD"

        color = "green" if pnl_pos >= 0 else "red"
        table.add_row(
            ticker,
            f"{pos.shares:.0f}",
            f"{cur}{pos.avg_cost:,.2f}",
            f"{cur}{price:,.2f}",
            f"[{color}]{'+' if pnl_pos>=0 else ''}{_fmt(pnl_pos)}[/]",
            f"[{color}]{'+' if pnl_pct>=0 else ''}{pnl_pct:.1f}%[/]",
            f"[dim]{cur}{stop:,.2f}[/]",
        )

    console.print(table)


def render_trades(trades: list[dict], limit: int = 20):
    if not trades:
        console.print("[dim]取引履歴なし[/]")
        return

    table = Table(title="[bold]取引履歴[/]", box=box.SIMPLE, header_style="bold")
    table.add_column("日時", width=20)
    table.add_column("銘柄", width=10)
    table.add_column("売買", width=6)
    table.add_column("株数", justify="right", width=8)
    table.add_column("価格", justify="right", width=12)
    table.add_column("戦略/理由", width=20)

    for t in trades[:limit]:
        side_color = "green" if t["side"] == "BUY" else "red"
        cur = "JPY" if t["ticker"].endswith(".T") else "USD"
        table.add_row(
            t["executed_at"][:19],
            t["ticker"],
            f"[{side_color}]{t['side']}[/]",
            f"{t['shares']:.0f}",
            f"{cur}{t['price']:,.2f}",
            t.get("strategy", ""),
        )
    console.print(table)


def _fmt(value: float) -> str:
    """数値を読みやすい形式にフォーマット"""
    sign = "" if value >= 0 else "-"
    abs_val = abs(value)
    if abs_val >= 1_000_000:
        return f"{sign}JPY{abs_val/1_000_000:.2f}M"
    return f"{sign}JPY{abs_val:,.0f}"
