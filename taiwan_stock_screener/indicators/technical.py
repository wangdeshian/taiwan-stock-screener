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


def bollinger_squeeze_signal(
    prices: pd.DataFrame,
    lookback_days: int = 120,
    extreme_percentile: float = 5,
    volume_avg_days: int = 5,
    volume_min_ratio: float = 1.5,
    volume_max_ratio: float = 3,
) -> dict[str, float | bool] | None:
    """布林通道極度壓縮＋溫和點火的海選訊號（左側潛伏第一階段用）。

    三大觸發條件（交集）：
    1. 絕對壓縮：今日帶寬處於近 lookback_days 的最低 extreme_percentile 百分位內
    2. 溫和點火：今日量為前 volume_avg_days 日均量的 min~max 倍（排除已噴出爆量股）
    3. 趨勢確認：收盤 > 20MA 且收紅（Close > Open）

    回傳 dict（含各條件布林值與 is_squeeze_trigger 交集結果），資料不足時回傳 None。
    """
    required = {"open", "close", "volume"}
    if prices.empty or not required.issubset(prices.columns):
        return None
    df = prices.sort_values("trade_date") if "trade_date" in prices.columns else prices
    close = df["close"].astype(float)
    if len(close) < 40:
        return None

    middle = close.rolling(window=20, min_periods=20).mean()
    std = close.rolling(window=20, min_periods=20).std()
    safe_middle = middle.where(middle != 0)
    bandwidth = (4 * std) / safe_middle  # (upper-lower)/middle = 4σ/MA20

    recent = bandwidth.tail(lookback_days).dropna()
    if len(recent) < 20:
        return None
    percentile = float((recent <= recent.iloc[-1]).mean() * 100)
    is_extreme_squeeze = percentile <= extreme_percentile

    volumes = df["volume"].astype(float)
    prior_avg = float(volumes.iloc[-(volume_avg_days + 1):-1].mean()) if len(volumes) > volume_avg_days else 0.0
    volume_ratio = float(volumes.iloc[-1]) / prior_avg if prior_avg > 0 else 0.0
    is_mild_ignition = volume_min_ratio <= volume_ratio < volume_max_ratio

    last_close = float(close.iloc[-1])
    last_open = float(df["open"].astype(float).iloc[-1])
    last_ma20 = float(middle.iloc[-1]) if pd.notna(middle.iloc[-1]) else 0.0
    is_bullish_confirmation = last_close > last_ma20 and last_close > last_open

    return {
        "bandwidth_percentile": round(percentile, 1),
        "volume_ratio": round(volume_ratio, 2),
        "is_extreme_squeeze": is_extreme_squeeze,
        "is_mild_ignition": is_mild_ignition,
        "is_bullish_confirmation": is_bullish_confirmation,
        "is_squeeze_trigger": is_extreme_squeeze and is_mild_ignition and is_bullish_confirmation,
    }


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
