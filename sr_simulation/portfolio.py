"""仮想ポートフォリオ状態管理"""
import logging
from dataclasses import dataclass, field

from sr_data import db

logger = logging.getLogger(__name__)


@dataclass
class Position:
    ticker: str
    shares: float
    avg_cost: float
    peak_price: float  # トレーリングストップ用の高値追跡


class Portfolio:
    def __init__(self, session_id: str, initial_cash: float = 1_000_000.0):
        self.session_id = session_id
        self.initial_cash = initial_cash
        self._cash = db.get_cash(session_id, initial_cash)
        self._positions: dict[str, Position] = {}
        self._load_positions()

    def _load_positions(self):
        for row in db.get_positions(self.session_id):
            self._positions[row["ticker"]] = Position(
                ticker=row["ticker"],
                shares=row["shares"],
                avg_cost=row["avg_cost"],
                peak_price=row["peak_price"],
            )

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self) -> dict[str, Position]:
        return self._positions

    def market_value(self, current_prices: dict[str, float]) -> float:
        return sum(
            pos.shares * current_prices.get(t, pos.avg_cost)
            for t, pos in self._positions.items()
        )

    def total_equity(self, current_prices: dict[str, float]) -> float:
        return self._cash + self.market_value(current_prices)

    def unrealized_pnl(self, current_prices: dict[str, float]) -> float:
        return sum(
            pos.shares * (current_prices.get(t, pos.avg_cost) - pos.avg_cost)
            for t, pos in self._positions.items()
        )

    def buy(self, ticker: str, shares: float, price: float, strategy: str = "",
            session_type: str = "paper") -> bool:
        cost = shares * price
        if cost > self._cash:
            logger.warning(f"[portfolio] 資金不足: {ticker} {shares}株 @{price:.2f} (必要={cost:.0f}, 残高={self._cash:.0f})")
            return False

        self._cash -= cost
        db.set_cash(self.session_id, self._cash)

        if ticker in self._positions:
            pos = self._positions[ticker]
            total_shares = pos.shares + shares
            new_avg = (pos.shares * pos.avg_cost + shares * price) / total_shares
            self._positions[ticker] = Position(ticker, total_shares, new_avg, max(pos.peak_price, price))
        else:
            self._positions[ticker] = Position(ticker, shares, price, price)

        pos = self._positions[ticker]
        db.upsert_position(self.session_id, ticker, pos.shares, pos.avg_cost, pos.peak_price)
        db.save_trade(self.session_id, session_type, ticker, "BUY", shares, price, strategy)
        logger.info(f"[portfolio] BUY {ticker} {shares}株 @{price:.2f} | 残高={self._cash:.0f}")
        return True

    def sell(self, ticker: str, shares: float, price: float, strategy: str = "",
             session_type: str = "paper") -> bool:
        if ticker not in self._positions or self._positions[ticker].shares < shares:
            logger.warning(f"[portfolio] 保有不足: {ticker} {shares}株を売れない")
            return False

        self._cash += shares * price
        db.set_cash(self.session_id, self._cash)

        pos = self._positions[ticker]
        remaining = pos.shares - shares
        if remaining <= 0:
            del self._positions[ticker]
            db.upsert_position(self.session_id, ticker, 0, 0, 0)
        else:
            self._positions[ticker] = Position(ticker, remaining, pos.avg_cost, pos.peak_price)
            p = self._positions[ticker]
            db.upsert_position(self.session_id, ticker, p.shares, p.avg_cost, p.peak_price)

        db.save_trade(self.session_id, session_type, ticker, "SELL", shares, price, strategy)
        pnl = shares * (price - pos.avg_cost)
        logger.info(f"[portfolio] SELL {ticker} {shares}株 @{price:.2f} | 損益={pnl:+.0f} | 残高={self._cash:.0f}")
        return True

    def update_peak(self, ticker: str, current_price: float):
        """保有中の高値を更新（トレーリングストップ用）"""
        if ticker in self._positions:
            pos = self._positions[ticker]
            if current_price > pos.peak_price:
                self._positions[ticker] = Position(ticker, pos.shares, pos.avg_cost, current_price)
                db.upsert_position(self.session_id, ticker, pos.shares, pos.avg_cost, current_price)
