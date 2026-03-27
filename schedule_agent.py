"""
StockRadar スケジュールエージェント
/schedule から呼び出されるスタンドアロンスクリプト。
portfolio.json を読み書きし、売買判断・レポート生成を行う。
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ── 依存パッケージを自動インストール ──────────────────────────
subprocess.run([sys.executable, "-m", "pip", "install",
                "yfinance", "pandas", "pytz", "-q"], check=False)

import warnings
warnings.filterwarnings("ignore")
import pandas as pd
import yfinance as yf

# ── 設定 ──────────────────────────────────────────────────────
PORTFOLIO_FILE = Path(__file__).parent / "portfolio.json"
WATCHLIST = {
    "7203.T": "トヨタ自動車",
    "6758.T": "ソニーグループ",
    "9984.T": "ソフトバンクG",
    "NVDA":   "NVIDIA",
    "INTC":   "Intel",
    "SOFI":   "SoFi Technologies",
}
STOP_LOSS_PCT      = 0.07   # 損切りライン -7%
TRAILING_STOP_PCT  = 0.05   # トレーリングストップ -5%
MAX_POSITION_PCT   = 0.30   # 1銘柄最大 30%
MIN_CASH_RESERVE   = 0.10   # キャッシュ予備 10%
USDJPY_FALLBACK    = 150.0

# ── ユーティリティ ────────────────────────────────────────────

def load_portfolio() -> dict:
    if PORTFOLIO_FILE.exists():
        with open(PORTFOLIO_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"session_id": "default", "initial_cash": 100000,
            "cash": 100000, "positions": {}, "trades": []}


def save_portfolio(p: dict):
    p["last_updated"] = datetime.now().isoformat()
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=2, default=str)


def get_usdjpy() -> float:
    try:
        rate = float(yf.Ticker("USDJPY=X").fast_info.last_price or 0)
        return rate if rate > 0 else USDJPY_FALLBACK
    except Exception:
        return USDJPY_FALLBACK


def to_jpy(ticker: str, price: float, usdjpy: float) -> float:
    return price * usdjpy if not ticker.endswith(".T") else price


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def fetch_data(ticker: str) -> pd.DataFrame:
    try:
        df = yf.download(ticker, period="3mo", interval="1d",
                         auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]
        return df.dropna() if not df.empty else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def generate_signal(df: pd.DataFrame) -> str:
    """composite戦略: MAクロス + RSI確認"""
    if len(df) < 26:
        return "HOLD"
    close = df["close"]
    ma5  = close.rolling(5).mean()
    ma25 = close.rolling(25).mean()
    rsi  = calc_rsi(close)

    prev_ma5, curr_ma5   = float(ma5.iloc[-2]),  float(ma5.iloc[-1])
    prev_ma25, curr_ma25 = float(ma25.iloc[-2]), float(ma25.iloc[-1])
    curr_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

    if prev_ma5 < prev_ma25 and curr_ma5 >= curr_ma25:
        if curr_rsi > 45:
            return "BUY"
    if prev_ma5 > prev_ma25 and curr_ma5 <= curr_ma25:
        return "SELL"
    if curr_rsi >= 70:
        return "SELL"
    return "HOLD"


def calc_shares(cash: float, jpy_price: float, total_equity: float) -> int:
    spendable = min(cash * (1 - MIN_CASH_RESERVE),
                    total_equity * MAX_POSITION_PCT)
    return max(0, int(spendable / jpy_price))


# ── メイン処理 ────────────────────────────────────────────────

def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*62}")
    print(f"  StockRadar 朝次レポート  {now} JST")
    print(f"{'='*62}")

    portfolio = load_portfolio()
    usdjpy = get_usdjpy()
    print(f"  USD/JPY: {usdjpy:.1f}\n")

    market_data = {}
    signals = {}

    # ── 株価取得・指標計算 ─────────────────────────────────────
    for ticker, name in WATCHLIST.items():
        df = fetch_data(ticker)
        if df.empty or len(df) < 26:
            print(f"  {name} ({ticker}): データ取得失敗")
            continue

        close       = df["close"]
        curr_price  = float(close.iloc[-1])
        prev_price  = float(close.iloc[-2])
        change_pct  = (curr_price - prev_price) / prev_price * 100
        rsi         = float(calc_rsi(close).iloc[-1])
        ma5         = float(close.rolling(5).mean().iloc[-1])
        ma25        = float(close.rolling(25).mean().iloc[-1])
        jpy_price   = to_jpy(ticker, curr_price, usdjpy)
        cur         = "JPY" if ticker.endswith(".T") else "USD"
        signal      = generate_signal(df)

        market_data[ticker] = {
            "name": name, "price": curr_price, "jpy_price": jpy_price,
            "currency": cur, "change_pct": change_pct,
            "rsi": rsi, "ma5": ma5, "ma25": ma25, "df": df,
        }
        signals[ticker] = signal

        sign = "+" if change_pct >= 0 else ""
        sig_icon = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(signal, "⚪")
        print(f"  {sig_icon} {name:<16} {cur}{curr_price:>10,.2f}  "
              f"({sign}{change_pct:.2f}%)  RSI:{rsi:.0f}  {signal}")

    # ── 損切り / トレーリングストップ ──────────────────────────
    executed = []
    positions = portfolio.get("positions", {})

    for ticker, pos in list(positions.items()):
        if ticker not in market_data:
            continue
        jpy_price  = market_data[ticker]["jpy_price"]
        avg_cost   = pos["avg_cost"]
        peak_price = pos.get("peak_price", avg_cost)

        # peak更新
        if jpy_price > peak_price:
            positions[ticker]["peak_price"] = jpy_price
            peak_price = jpy_price

        reason = None
        if avg_cost > 0 and (avg_cost - jpy_price) / avg_cost >= STOP_LOSS_PCT:
            reason = f"損切り(-{STOP_LOSS_PCT*100:.0f}%)"
        elif peak_price > 0 and (peak_price - jpy_price) / peak_price >= TRAILING_STOP_PCT:
            reason = f"トレーリングストップ(高値-{TRAILING_STOP_PCT*100:.0f}%)"

        if reason:
            proceeds = pos["shares"] * jpy_price
            pnl = pos["shares"] * (jpy_price - avg_cost)
            portfolio["cash"] += proceeds
            portfolio["trades"].append({
                "ticker": ticker, "side": "SELL",
                "shares": pos["shares"], "price": jpy_price,
                "pnl": pnl, "reason": reason,
                "executed_at": datetime.now().isoformat(),
            })
            executed.append(f"SELL {ticker} {pos['shares']:.0f}株 @JPY{jpy_price:,.0f} ({reason})")
            del positions[ticker]

    # ── 戦略シグナルによる売買 ─────────────────────────────────
    jpy_prices    = {t: d["jpy_price"] for t, d in market_data.items()}
    holdings_val  = sum(pos["shares"] * jpy_prices.get(t, pos["avg_cost"])
                        for t, pos in positions.items())
    total_equity  = portfolio["cash"] + holdings_val

    for ticker, signal in signals.items():
        if ticker not in market_data:
            continue
        jpy_price = market_data[ticker]["jpy_price"]

        if signal == "BUY" and ticker not in positions:
            shares = calc_shares(portfolio["cash"], jpy_price, total_equity)
            if shares > 0:
                cost = shares * jpy_price
                portfolio["cash"] -= cost
                positions[ticker] = {
                    "shares": shares, "avg_cost": jpy_price, "peak_price": jpy_price
                }
                portfolio["trades"].append({
                    "ticker": ticker, "side": "BUY",
                    "shares": shares, "price": jpy_price,
                    "pnl": 0, "reason": "composite戦略",
                    "executed_at": datetime.now().isoformat(),
                })
                executed.append(f"BUY  {ticker} {shares}株 @JPY{jpy_price:,.0f} (composite戦略)")

        elif signal == "SELL" and ticker in positions:
            pos = positions[ticker]
            proceeds = pos["shares"] * jpy_price
            pnl = pos["shares"] * (jpy_price - pos["avg_cost"])
            portfolio["cash"] += proceeds
            portfolio["trades"].append({
                "ticker": ticker, "side": "SELL",
                "shares": pos["shares"], "price": jpy_price,
                "pnl": pnl, "reason": "composite戦略",
                "executed_at": datetime.now().isoformat(),
            })
            executed.append(f"SELL {ticker} {pos['shares']:.0f}株 @JPY{jpy_price:,.0f} (composite戦略 PnL:{pnl:+.0f})")
            del positions[ticker]

    portfolio["positions"] = positions

    # ── ポートフォリオサマリー ─────────────────────────────────
    holdings_val = sum(pos["shares"] * jpy_prices.get(t, pos["avg_cost"])
                       for t, pos in positions.items())
    total_equity = portfolio["cash"] + holdings_val
    initial      = portfolio.get("initial_cash", 100000)
    total_return = (total_equity - initial) / initial * 100

    print(f"\n{'─'*62}")
    print(f"  【ポートフォリオ】")
    print(f"  総資産   JPY{total_equity:>10,.0f}  "
          f"({'+'if total_return>=0 else ''}{total_return:.2f}%)")
    print(f"  現金     JPY{portfolio['cash']:>10,.0f}")

    if positions:
        print(f"  保有銘柄:")
        for t, pos in positions.items():
            cur_p = jpy_prices.get(t, pos["avg_cost"])
            pnl_p = pos["shares"] * (cur_p - pos["avg_cost"])
            pnl_pct = (cur_p - pos["avg_cost"]) / pos["avg_cost"] * 100
            sign = "+" if pnl_p >= 0 else ""
            print(f"    {t:<8} {pos['shares']:.0f}株  "
                  f"取得JPY{pos['avg_cost']:,.0f} → JPY{cur_p:,.0f}  "
                  f"({sign}{pnl_p:,.0f} / {sign}{pnl_pct:.1f}%)")
    else:
        print("  保有銘柄: なし（全キャッシュ）")

    # ── 本日の売買 ─────────────────────────────────────────────
    print(f"\n  【本日の売買】")
    if executed:
        for e in executed:
            print(f"    {e}")
    else:
        print("    シグナルなし（HOLD）")

    # ── 注目ポイント ───────────────────────────────────────────
    buys  = [t for t, s in signals.items() if s == "BUY"]
    sells = [t for t, s in signals.items() if s == "SELL"]
    low_rsi = [(t, d) for t, d in market_data.items() if d["rsi"] < 30]

    print(f"\n  【本日の注目】")
    if buys:
        names = [market_data[t]["name"] for t in buys if t in market_data]
        print(f"    🟢 買いシグナル: {', '.join(names)}")
    if sells:
        names = [market_data[t]["name"] for t in sells if t in market_data]
        print(f"    🔴 売りシグナル: {', '.join(names)}")
    if low_rsi:
        for t, d in low_rsi:
            print(f"    🟡 {d['name']} RSI={d['rsi']:.0f} 売られすぎ注意")
    if not buys and not sells and not low_rsi:
        print("    特になし。様子見が無難です。")

    print(f"\n{'='*62}\n")

    # ── 保存 & git push ────────────────────────────────────────
    save_portfolio(portfolio)
    print("portfolio.json を更新しました")

    try:
        subprocess.run(["git", "config", "user.email", "stockradar@auto"], check=False)
        subprocess.run(["git", "config", "user.name", "StockRadar Bot"], check=False)
        subprocess.run(["git", "add", "portfolio.json"], check=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], capture_output=True
        )
        if result.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m",
                 f"portfolio update {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
                check=True
            )
            subprocess.run(["git", "push"], check=True)
            print("GitHub に portfolio.json をプッシュしました")
        else:
            print("変更なし — プッシュをスキップ")
    except Exception as e:
        print(f"git push 失敗（手動確認を）: {e}")


if __name__ == "__main__":
    main()
