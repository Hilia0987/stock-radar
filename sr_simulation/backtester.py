"""バックテストエンジン（ルックアヘッドバイアスなし）"""
import logging
from dataclasses import dataclass, field

import pandas as pd

from sr_analysis import indicators
from sr_data import fetcher
from sr_simulation.strategy import (
    Signal, get_strategy,
    check_stop_loss, check_trailing_stop,
)

logger = logging.getLogger(__name__)

INITIAL_CASH = 1_000_000.0
STOP_LOSS_PCT = 0.07
TRAILING_STOP_PCT = 0.05
MAX_POSITION_PCT = 0.20
MIN_CASH_RESERVE_PCT = 0.10


@dataclass
class BacktestResult:
    ticker: str
    strategy_name: str
    initial_cash: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    num_trades: int
    win_rate_pct: float
    trades: list[dict] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)


def run(ticker: str, strategy_name: str = "composite",
        start: str = "2024-01-01", end: str = "",
        initial_cash: float = INITIAL_CASH) -> BacktestResult:

    strategy = get_strategy(strategy_name)

    # 履歴取得（DBまたはyfinance）
    df = fetcher.get_history_from_db(ticker)
    if df.empty:
        period = "2y" if not start else "max"
        df = fetcher.get_history(ticker, period=period)
    if df.empty:
        logger.error(f"[backtest] {ticker}: データ取得失敗")
        return _empty_result(ticker, strategy_name, initial_cash)

    df.index = pd.to_datetime(df.index)
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    if len(df) < 30:
        logger.warning(f"[backtest] {ticker}: データ不足 ({len(df)}行)")
        return _empty_result(ticker, strategy_name, initial_cash)

    cash = initial_cash
    shares = 0.0
    avg_cost = 0.0
    peak_price = 0.0
    trades = []
    equity_curve = []

    for i in range(26, len(df)):
        sub = df.iloc[:i+1].copy()
        sub = indicators.compute_all(sub)
        price = float(sub["close"].iloc[-1])
        ts = sub.index[-1]

        # 損切り / トレーリングストップ（保有時のみ）
        if shares > 0:
            peak_price = max(peak_price, price)
            if check_stop_loss(avg_cost, price, STOP_LOSS_PCT):
                pnl = shares * (price - avg_cost)
                trades.append({"ts": ts, "side": "SELL", "price": price,
                                "shares": shares, "pnl": pnl, "reason": "stop_loss"})
                cash += shares * price
                shares, avg_cost, peak_price = 0.0, 0.0, 0.0
            elif check_trailing_stop(peak_price, price, TRAILING_STOP_PCT):
                pnl = shares * (price - avg_cost)
                trades.append({"ts": ts, "side": "SELL", "price": price,
                                "shares": shares, "pnl": pnl, "reason": "trailing_stop"})
                cash += shares * price
                shares, avg_cost, peak_price = 0.0, 0.0, 0.0

        # 戦略シグナル（i+1 の始値で執行 → ルックアヘッドバイアス回避）
        signal = strategy.generate_signal(ticker, sub)
        exec_price = float(df["open"].iloc[i]) if i + 1 < len(df) else price

        if signal == Signal.BUY and shares == 0:
            equity = cash + shares * exec_price
            spendable = min(cash * (1 - MIN_CASH_RESERVE_PCT),
                            equity * MAX_POSITION_PCT)
            new_shares = int(spendable / exec_price)
            if new_shares > 0:
                cost = new_shares * exec_price
                cash -= cost
                avg_cost = exec_price
                shares = new_shares
                peak_price = exec_price
                trades.append({"ts": ts, "side": "BUY", "price": exec_price,
                                "shares": new_shares, "pnl": 0, "reason": strategy_name})

        elif signal == Signal.SELL and shares > 0:
            pnl = shares * (exec_price - avg_cost)
            trades.append({"ts": ts, "side": "SELL", "price": exec_price,
                            "shares": shares, "pnl": pnl, "reason": strategy_name})
            cash += shares * exec_price
            shares, avg_cost, peak_price = 0.0, 0.0, 0.0

        equity_curve.append(cash + shares * price)

    # 最終清算
    final_price = float(df["close"].iloc[-1])
    if shares > 0:
        cash += shares * final_price
    final_equity = cash

    equity_series = pd.Series(equity_curve, index=df.index[26:])
    total_return = (final_equity - initial_cash) / initial_cash * 100
    max_dd = _max_drawdown(equity_series)
    sharpe = _sharpe(equity_series)

    sells = [t for t in trades if t["side"] == "SELL"]
    wins = [t for t in sells if t["pnl"] > 0]
    win_rate = (len(wins) / len(sells) * 100) if sells else 0.0

    return BacktestResult(
        ticker=ticker,
        strategy_name=strategy_name,
        initial_cash=initial_cash,
        final_equity=final_equity,
        total_return_pct=round(total_return, 2),
        max_drawdown_pct=round(max_dd, 2),
        sharpe_ratio=round(sharpe, 3),
        num_trades=len(trades),
        win_rate_pct=round(win_rate, 1),
        trades=trades,
        equity_curve=equity_series,
    )


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak * 100
    return float(dd.min())


def _sharpe(equity: pd.Series, risk_free: float = 0.01) -> float:
    if len(equity) < 2:
        return 0.0
    daily_ret = equity.pct_change().dropna()
    excess = daily_ret - risk_free / 252
    std = excess.std()
    if std == 0:
        return 0.0
    return float(excess.mean() / std * (252 ** 0.5))


def _empty_result(ticker, strategy_name, initial_cash):
    return BacktestResult(
        ticker=ticker, strategy_name=strategy_name,
        initial_cash=initial_cash, final_equity=initial_cash,
        total_return_pct=0.0, max_drawdown_pct=0.0, sharpe_ratio=0.0,
        num_trades=0, win_rate_pct=0.0,
    )
