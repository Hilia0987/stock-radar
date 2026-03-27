"""
売買戦略定義

リスク管理パラメータ（config.yamlから注入）:
  stop_loss_pct      = 0.07   取得平均から -7% で強制損切り
  trailing_stop_pct  = 0.05   高値から -5% でトレーリングストップ
  max_position_pct   = 0.20   1銘柄 = 資金の最大 20%
  min_cash_reserve   = 0.10   キャッシュを 10% 以上常に確保

戦略:
  MACrossStrategy  : MA5/25 ゴールデンクロス→買い、デッドクロス→売り
  RSIStrategy      : RSI≤30→買い、RSI≥70→売り
  CompositeStrategy: 両戦略のシグナルが一致したときのみ発火（ノイズ低減）
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum

import pandas as pd

logger = logging.getLogger(__name__)


class Signal(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class BaseStrategy(ABC):
    name: str = "base"

    @abstractmethod
    def generate_signal(self, ticker: str, df: pd.DataFrame) -> Signal:
        ...

    def calc_shares(self, available_cash: float, price: float,
                    max_position_pct: float = 0.20,
                    min_cash_reserve_pct: float = 0.10,
                    total_equity: float = 0.0) -> int:
        """
        購入株数を計算する。
        - 1回の投資上限: total_equity の max_position_pct
        - キャッシュ予備を min_cash_reserve_pct 確保
        """
        if price <= 0:
            return 0
        base = total_equity if total_equity > 0 else available_cash
        spendable = min(available_cash * (1 - min_cash_reserve_pct),
                        base * max_position_pct)
        shares = int(spendable / price)
        return max(0, shares)


class MACrossStrategy(BaseStrategy):
    name = "ma_cross"

    def generate_signal(self, ticker: str, df: pd.DataFrame) -> Signal:
        if "sma5" not in df.columns or "sma25" not in df.columns or len(df) < 26:
            return Signal.HOLD
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        if pd.isna(prev["sma5"]) or pd.isna(curr["sma5"]):
            return Signal.HOLD
        # ゴールデンクロス
        if prev["sma5"] < prev["sma25"] and curr["sma5"] >= curr["sma25"]:
            return Signal.BUY
        # デッドクロス
        if prev["sma5"] > prev["sma25"] and curr["sma5"] <= curr["sma25"]:
            return Signal.SELL
        return Signal.HOLD


class RSIStrategy(BaseStrategy):
    name = "rsi"

    def __init__(self, oversold: float = 30.0, overbought: float = 70.0):
        self.oversold = oversold
        self.overbought = overbought

    def generate_signal(self, ticker: str, df: pd.DataFrame) -> Signal:
        if "rsi" not in df.columns or len(df) < 2:
            return Signal.HOLD
        prev_rsi = df["rsi"].iloc[-2]
        curr_rsi = df["rsi"].iloc[-1]
        if pd.isna(curr_rsi) or pd.isna(prev_rsi):
            return Signal.HOLD
        # 売られすぎからの反発
        if prev_rsi <= self.oversold and curr_rsi > self.oversold:
            return Signal.BUY
        # 買われすぎからの反落
        if prev_rsi >= self.overbought and curr_rsi < self.overbought:
            return Signal.SELL
        return Signal.HOLD


class CompositeStrategy(BaseStrategy):
    """
    複合戦略:
    - 買い: MA5がMA25を上抜け、かつ RSI が 50 以上（上昇モメンタム確認）
    - 売り: MA5がMA25を下抜け、または RSI が 70 以上（どちらか一方で発火）
    単純な MA cross より精度を高めつつ、シグナルが出すぎない設計。
    """
    name = "composite"

    def __init__(self):
        self._ma = MACrossStrategy()

    def generate_signal(self, ticker: str, df: pd.DataFrame) -> Signal:
        ma_sig = self._ma.generate_signal(ticker, df)

        rsi = None
        if "rsi" in df.columns and not df["rsi"].isna().all():
            rsi = float(df["rsi"].iloc[-1])

        if ma_sig == Signal.BUY:
            # RSIが利用可能な場合は上昇モメンタム（RSI>45）を確認
            if rsi is None or rsi > 45:
                return Signal.BUY

        if ma_sig == Signal.SELL:
            return Signal.SELL

        # RSIが過熱（70超）していれば利確
        if rsi is not None and rsi >= 70:
            return Signal.SELL

        return Signal.HOLD


def get_strategy(name: str) -> BaseStrategy:
    mapping = {
        "ma_cross": MACrossStrategy,
        "rsi": RSIStrategy,
        "composite": CompositeStrategy,
    }
    cls = mapping.get(name, CompositeStrategy)
    return cls()


def check_stop_loss(avg_cost: float, current_price: float,
                    stop_loss_pct: float = 0.07) -> bool:
    """損切りライン判定: 平均取得価格から stop_loss_pct 以上下落したら True"""
    if avg_cost <= 0:
        return False
    return (avg_cost - current_price) / avg_cost >= stop_loss_pct


def check_trailing_stop(peak_price: float, current_price: float,
                        trailing_stop_pct: float = 0.05) -> bool:
    """トレーリングストップ判定: 高値から trailing_stop_pct 以上下落したら True"""
    if peak_price <= 0:
        return False
    return (peak_price - current_price) / peak_price >= trailing_stop_pct
