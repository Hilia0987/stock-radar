"""アラートルール評価"""
import logging

import pandas as pd

from sr_data import db
from sr_analysis.anomaly import detect_volume_spike

logger = logging.getLogger(__name__)


def evaluate(ticker: str, quote: dict, df: pd.DataFrame, rules: list[dict]) -> list[dict]:
    """
    quote と df（指標付き）に対してルールを評価し、
    発火したアラートをDBに保存して返す。
    """
    triggered = []
    price = quote.get("price", 0)

    for rule in rules:
        rule_type = rule["rule_type"]
        threshold = float(rule["threshold"])
        fired = False
        value = 0.0

        if rule_type == "price_above" and price >= threshold:
            fired, value = True, price
        elif rule_type == "price_below" and price <= threshold:
            fired, value = True, price
        elif rule_type == "rsi_oversold":
            if "rsi" in df.columns and not df["rsi"].isna().all():
                rsi = float(df["rsi"].iloc[-1])
                if rsi <= threshold:
                    fired, value = True, rsi
        elif rule_type == "rsi_overbought":
            if "rsi" in df.columns and not df["rsi"].isna().all():
                rsi = float(df["rsi"].iloc[-1])
                if rsi >= threshold:
                    fired, value = True, rsi
        elif rule_type == "volume_spike":
            is_spike, ratio = detect_volume_spike(df, threshold=threshold)
            if is_spike:
                fired, value = True, ratio

        if fired:
            db.save_alert(ticker, rule_type, value, threshold)
            triggered.append({
                "ticker": ticker,
                "rule_type": rule_type,
                "value": value,
                "threshold": threshold,
            })
            logger.info(f"[alert] {ticker} {rule_type} fired: {value:.2f} (threshold={threshold})")

    return triggered
