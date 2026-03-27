"""テクニカル指標計算（pandas-ta使用）"""
import pandas as pd

try:
    import pandas_ta as ta
    _HAS_TA = True
except ImportError:
    _HAS_TA = False


def _sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def _ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()


def add_ma(df: pd.DataFrame, windows: list[int] = [5, 25, 75]) -> pd.DataFrame:
    for w in windows:
        if _HAS_TA:
            df[f"sma{w}"] = ta.sma(df["close"], length=w)
        else:
            df[f"sma{w}"] = _sma(df["close"], w)
    return df


def add_rsi(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    if _HAS_TA:
        df["rsi"] = ta.rsi(df["close"], length=length)
    else:
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(length).mean()
        loss = (-delta.clip(upper=0)).rolling(length).mean()
        rs = gain / loss.replace(0, 1e-10)
        df["rsi"] = 100 - (100 / (1 + rs))
    return df


def add_macd(df: pd.DataFrame) -> pd.DataFrame:
    if _HAS_TA:
        macd = ta.macd(df["close"])
        if macd is not None and not macd.empty:
            df["macd"] = macd.iloc[:, 0]
            df["macd_signal"] = macd.iloc[:, 1]
            df["macd_hist"] = macd.iloc[:, 2]
    else:
        ema12 = _ema(df["close"], 12)
        ema26 = _ema(df["close"], 26)
        df["macd"] = ema12 - ema26
        df["macd_signal"] = _ema(df["macd"], 9)
        df["macd_hist"] = df["macd"] - df["macd_signal"]
    return df


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """全指標を一括計算して返す"""
    if df.empty or len(df) < 26:
        return df
    df = df.copy()
    df = add_ma(df)
    df = add_rsi(df)
    df = add_macd(df)
    return df
