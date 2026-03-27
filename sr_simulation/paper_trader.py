"""リアルタイムペーパートレードエンジン"""
import logging
from datetime import datetime

import pandas as pd

from sr_analysis import indicators
from sr_data import fetcher
from sr_simulation.portfolio import Portfolio
from sr_simulation.strategy import (
    Signal, get_strategy,
    check_stop_loss, check_trailing_stop,
)

logger = logging.getLogger(__name__)


def _jpy_price(ticker: str, native_price: float) -> float:
    """米国株の場合のみ USD→JPY 換算して返す"""
    if fetcher.is_us_ticker(ticker):
        return fetcher.to_jpy(native_price)
    return native_price


class PaperTrader:
    def __init__(self, config: dict):
        pt_cfg = config.get("paper_trading", {})
        risk_cfg = config.get("risk", {})

        self.session_id = pt_cfg.get("session_id", "default")
        self.strategy = get_strategy(pt_cfg.get("strategy", "composite"))
        self.portfolio = Portfolio(
            session_id=self.session_id,
            initial_cash=pt_cfg.get("initial_cash", 100_000),
        )

        self.stop_loss_pct = risk_cfg.get("stop_loss_pct", 0.07)
        self.trailing_stop_pct = risk_cfg.get("trailing_stop_pct", 0.05)
        self.max_position_pct = risk_cfg.get("max_position_pct", 0.25)
        self.min_cash_reserve_pct = risk_cfg.get("min_cash_reserve_pct", 0.10)

    def tick(self, tickers: list[str]) -> list[dict]:
        """1スキャンサイクル分の売買判断を実行して取引ログを返す"""
        quotes = fetcher.get_batch_quotes(tickers)
        executed = []

        # ─── 1. 保有銘柄の損切り / トレーリングストップチェック ───
        for ticker, pos in list(self.portfolio.positions.items()):
            quote = quotes.get(ticker)
            if not quote:
                continue
            # avg_cost はJPY建てで保存されているので、比較もJPYで行う
            jpy_price = _jpy_price(ticker, quote["price"])
            if jpy_price <= 0:
                continue

            self.portfolio.update_peak(ticker, jpy_price)

            reason = None
            if check_stop_loss(pos.avg_cost, jpy_price, self.stop_loss_pct):
                reason = f"損切り(-{self.stop_loss_pct*100:.0f}%)"
            elif check_trailing_stop(pos.peak_price, jpy_price, self.trailing_stop_pct):
                reason = f"トレーリングストップ(高値から-{self.trailing_stop_pct*100:.0f}%)"

            if reason:
                ok = self.portfolio.sell(
                    ticker, pos.shares, jpy_price,
                    strategy=reason, session_type="paper"
                )
                if ok:
                    executed.append({
                        "action": "SELL", "ticker": ticker,
                        "shares": pos.shares, "price": jpy_price,
                        "reason": reason, "ts": datetime.now().isoformat(),
                    })

        # ─── 2. 全ウォッチ銘柄に戦略シグナルを適用 ───
        jpy_prices = {t: _jpy_price(t, q["price"]) for t, q in quotes.items()}
        total_equity = self.portfolio.total_equity(jpy_prices)

        for ticker in tickers:
            quote = quotes.get(ticker)
            if not quote:
                continue
            jpy_price = jpy_prices.get(ticker, 0)
            if jpy_price <= 0:
                continue

            df = fetcher.get_history_from_db(ticker)
            if df.empty or len(df) < 26:
                df = fetcher.get_history(ticker)
            if df.empty or len(df) < 26:
                continue

            df = indicators.compute_all(df)
            signal = self.strategy.generate_signal(ticker, df)

            if signal == Signal.BUY and ticker not in self.portfolio.positions:
                shares = self.strategy.calc_shares(
                    self.portfolio.cash, jpy_price,
                    self.max_position_pct, self.min_cash_reserve_pct, total_equity
                )
                if shares > 0:
                    ok = self.portfolio.buy(
                        ticker, shares, jpy_price,
                        strategy=self.strategy.name, session_type="paper"
                    )
                    if ok:
                        executed.append({
                            "action": "BUY", "ticker": ticker,
                            "shares": shares, "price": jpy_price,
                            "reason": f"戦略シグナル({self.strategy.name})",
                            "ts": datetime.now().isoformat(),
                        })

            elif signal == Signal.SELL and ticker in self.portfolio.positions:
                pos = self.portfolio.positions[ticker]
                ok = self.portfolio.sell(
                    ticker, pos.shares, jpy_price,
                    strategy=self.strategy.name, session_type="paper"
                )
                if ok:
                    executed.append({
                        "action": "SELL", "ticker": ticker,
                        "shares": pos.shares, "price": jpy_price,
                        "reason": f"戦略シグナル({self.strategy.name})",
                        "ts": datetime.now().isoformat(),
                    })

        return executed
