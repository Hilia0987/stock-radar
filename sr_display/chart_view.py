"""ターミナルチャート表示（plotext）"""
import pandas as pd
import plotext as plt

from sr_display import shared_console as console


def render(ticker: str, df: pd.DataFrame, period: str = ""):
    if df.empty or len(df) < 5:
        console.print(f"[yellow]{ticker}: チャート描画に必要なデータがありません[/]")
        return

    closes = df["close"].dropna().tolist()
    n = len(closes)
    xs = list(range(n))

    # ── 終値 + MA チャート ──
    plt.clf()
    plt.theme("dark")
    plt.title(f"{ticker}  close  ({period})")
    plt.ylabel("price")
    plt.xlabel("days")
    plt.plot(xs, closes, color="cyan", label="close")

    if "sma5" in df.columns:
        vals = df["sma5"].dropna().tolist()
        offset = n - len(vals)
        plt.plot(list(range(offset, n)), vals, color="yellow", label="MA5")
    if "sma25" in df.columns:
        vals = df["sma25"].dropna().tolist()
        offset = n - len(vals)
        plt.plot(list(range(offset, n)), vals, color="orange", label="MA25")

    plt.show()

    # ── RSI サブチャート ──
    if "rsi" in df.columns:
        rsi_vals = df["rsi"].dropna().tolist()
        rsi_xs = list(range(n - len(rsi_vals), n))
        plt.clf()
        plt.theme("dark")
        plt.title(f"{ticker}  RSI(14)")
        plt.ylabel("RSI")
        plt.xlabel("days")
        plt.plot(rsi_xs, rsi_vals, color="magenta", label="RSI")
        plt.hline(70, color="red")
        plt.hline(30, color="green")
        plt.ylim(0, 100)
        plt.show()
