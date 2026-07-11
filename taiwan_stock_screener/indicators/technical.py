from __future__ import annotations

import numpy as np
import pandas as pd


def add_technical_indicators(prices: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"Missing columns for indicators: {sorted(missing)}")

    df = prices.sort_values("trade_date").copy()
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    for window in (5, 10, 20, 60, 120, 240):
        df[f"ma{window}"] = close.rolling(window=window, min_periods=1).mean()

    for span in (12, 20, 26):
        df[f"ema{span}"] = close.ewm(span=span, adjust=False).mean()

    df["rsi14"] = rsi(close, 14)
    macd_line, signal_line, histogram = macd(close)
    df["macd"] = macd_line
    df["macd_signal"] = signal_line
    df["macd_histogram"] = histogram

    k, d = kd(high, low, close)
    df["kd_k"] = k
    df["kd_d"] = d
    df["atr14"] = atr(high, low, close)
    upper, middle, lower = bollinger_bands(close)
    df["bb_upper"] = upper
    df["bb_middle"] = middle
    df["bb_lower"] = lower
    df["volume_ma20"] = volume.rolling(window=20, min_periods=1).mean()
    df["volume_ratio"] = np.where(df["volume_ma20"] > 0, volume / df["volume_ma20"], 0)
    df["high_60"] = high.rolling(window=60, min_periods=1).max()
    df["distance_from_60d_high_pct"] = np.where(df["high_60"] > 0, (df["high_60"] - close) / df["high_60"] * 100, 0)
    df["obv"] = obv(close, volume)
    df["adx14"] = adx(high, low, close)
    typical_price = (high + low + close) / 3
    cumulative_volume = volume.cumsum()
    df["vwap"] = np.where(cumulative_volume > 0, (typical_price * volume).cumsum() / cumulative_volume, close)
    return df


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window=window, min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(window=window, min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    return result.fillna(50)


def macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast = close.ewm(span=12, adjust=False).mean()
    slow = close.ewm(span=26, adjust=False).mean()
    macd_line = fast - slow
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def kd(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 9) -> tuple[pd.Series, pd.Series]:
    lowest_low = low.rolling(window=window, min_periods=1).min()
    highest_high = high.rolling(window=window, min_periods=1).max()
    rsv = (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan) * 100
    k = rsv.fillna(50).ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    return k, d


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(window=window, min_periods=1).mean()


def bollinger_bands(close: pd.Series, window: int = 20, std_multiplier: float = 2) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = close.rolling(window=window, min_periods=1).mean()
    std = close.rolling(window=window, min_periods=1).std().fillna(0)
    upper = middle + std * std_multiplier
    lower = middle - std * std_multiplier
    return upper, middle, lower


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
    atr_series = atr(high, low, close, window).replace(0, np.nan)
    plus_di = 100 * plus_dm.rolling(window=window, min_periods=1).mean() / atr_series
    minus_di = 100 * minus_dm.rolling(window=window, min_periods=1).mean() / atr_series
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.rolling(window=window, min_periods=1).mean().fillna(0)
