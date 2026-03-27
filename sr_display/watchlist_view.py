"""ウォッチリスト表示（rich）"""
from datetime import datetime

from rich.table import Table
from rich import box
from sr_display import shared_console as console


def render(watchlist: list[dict], quotes: dict[str, dict], indicators_map: dict[str, dict] = {}):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    table = Table(
        title=f"[bold cyan]Watchlist[/]  {now}",
        box=box.ROUNDED,
        show_lines=False,
        header_style="bold white on dark_blue",
    )

    table.add_column("Ticker", style="bold", width=10)
    table.add_column("銘柄名", width=14)
    table.add_column("現在値", justify="right", width=12)
    table.add_column("前日比", justify="right", width=10)
    table.add_column("RSI", justify="right", width=6)
    table.add_column("MACD", justify="right", width=8)
    table.add_column("出来高", justify="right", width=8)

    for item in watchlist:
        ticker = item["ticker"]
        name = item.get("display_name", ticker)
        q = quotes.get(ticker)
        ind = indicators_map.get(ticker, {})

        if not q or q.get("price", 0) == 0:
            table.add_row(ticker, name, "[dim]--[/]", "[dim]--[/]", "--", "--", "--")
            continue

        price = q["price"]
        change_pct = q.get("change_pct", 0)
        currency = "JPY" if ticker.endswith(".T") else "USD"

        price_str = f"{currency}{price:,.2f}"
        if change_pct > 0:
            chg_str = f"[green]+{change_pct:.2f}%[/]"
        elif change_pct < 0:
            chg_str = f"[red]{change_pct:.2f}%[/]"
        else:
            chg_str = f"{change_pct:.2f}%"

        rsi = ind.get("rsi")
        if rsi is None:
            rsi_str = "--"
        elif rsi <= 30:
            rsi_str = f"[green]{rsi:.1f}[/]"
        elif rsi >= 70:
            rsi_str = f"[red]{rsi:.1f}[/]"
        else:
            rsi_str = f"{rsi:.1f}"

        macd = ind.get("macd_hist")
        if macd is None:
            macd_str = "--"
        elif macd > 0:
            macd_str = f"[green]+{macd:.2f}[/]"
        else:
            macd_str = f"[red]{macd:.2f}[/]"

        vol = q.get("volume", 0)
        vol_str = _fmt_volume(vol)
        spike = ind.get("volume_spike", False)
        if spike:
            vol_str += " [yellow]⚠[/]"

        table.add_row(ticker, name, price_str, chg_str, rsi_str, macd_str, vol_str)

    console.print(table)


def _fmt_volume(vol: int) -> str:
    if vol >= 1_000_000:
        return f"{vol/1_000_000:.1f}M"
    if vol >= 1_000:
        return f"{vol/1_000:.0f}K"
    return str(vol)
