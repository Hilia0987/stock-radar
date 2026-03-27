"""異常検知（出来高スパイク・価格ギャップ）"""
import pandas as pd


def detect_volume_spike(df: pd.DataFrame, threshold: float = 2.5) -> tuple[bool, float]:
    """
    出来高が直近20日平均の threshold 倍以上なら True を返す。
    Returns: (is_spike, ratio)
    """
    if len(df) < 21:
        return False, 0.0
    avg_vol = df["volume"].iloc[-21:-1].mean()
    if avg_vol <= 0:
        return False, 0.0
    current_vol = df["volume"].iloc[-1]
    ratio = current_vol / avg_vol
    return ratio >= threshold, round(ratio, 2)


def detect_price_gap(df: pd.DataFrame, threshold: float = 0.03) -> float:
    """
    前日終値から本日始値へのギャップ率を返す。
    正値 = ギャップアップ、負値 = ギャップダウン
    """
    if len(df) < 2:
        return 0.0
    prev_close = df["close"].iloc[-2]
    today_open = df["open"].iloc[-1]
    if prev_close <= 0:
        return 0.0
    return (today_open - prev_close) / prev_close
