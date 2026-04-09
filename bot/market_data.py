"""
Market data fetcher for the trading bot (Quant Edition).
Collects BTC price, multi-timeframe technical indicators, volume analysis,
and Fear & Greed Index. Pure technical analysis — no news dependency.
All external API calls are wrapped in try/except for resilience.
"""

import logging
from typing import Optional

import pandas as pd
import ta as ta_lib
import requests
from binance.client import Client as BinanceClient

import config

logger = logging.getLogger(__name__)

# ── Binance Client ───────────────────────────────────────────────────────────

_binance: Optional[BinanceClient] = None


def get_binance_client() -> BinanceClient:
    """Return a singleton Binance client."""
    global _binance
    if _binance is None:
        _binance = BinanceClient(
            config.BINANCE_API_KEY,
            config.BINANCE_API_SECRET,
        )
    return _binance


# ── Price ────────────────────────────────────────────────────────────────────

def get_current_price() -> Optional[float]:
    """Fetch the current BTC/USDT price from Binance."""
    try:
        ticker = get_binance_client().get_symbol_ticker(symbol=config.SYMBOL)
        price = float(ticker["price"])
        logger.info(f"Current BTC price: ${price:,.2f}")
        return price
    except Exception as e:
        logger.error(f"Failed to fetch BTC price: {e}")
        return None


# ── Candle Data (Multi-Timeframe) ────────────────────────────────────────────

def get_candles(interval: str, limit: int = 100) -> Optional[pd.DataFrame]:
    """
    Fetch candles for BTC/USDT from Binance.

    Args:
        interval: Binance kline interval (e.g. KLINE_INTERVAL_1HOUR).
        limit: Number of candles to fetch.

    Returns:
        DataFrame with columns [open, high, low, close, volume], or None.
    """
    try:
        klines = get_binance_client().get_klines(
            symbol=config.SYMBOL,
            interval=interval,
            limit=limit,
        )
        df = pd.DataFrame(klines, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        return df
    except Exception as e:
        logger.error(f"Failed to fetch {interval} candles: {e}")
        return None


# ── Technical Indicators ─────────────────────────────────────────────────────

def calculate_rsi(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Calculate RSI from a candle DataFrame."""
    try:
        rsi = ta_lib.momentum.RSIIndicator(df["close"], window=period).rsi()
        return round(float(rsi.iloc[-1]), 2)
    except Exception as e:
        logger.error(f"Failed to calculate RSI: {e}")
        return None


def calculate_macd(df: pd.DataFrame) -> Optional[dict]:
    """
    Calculate MACD and return detailed info.

    Returns:
        Dict with keys: signal, macd_value, signal_value, histogram, crossover.
    """
    try:
        macd_ind = ta_lib.trend.MACD(df["close"])
        macd_line = float(macd_ind.macd().iloc[-1])
        signal_line = float(macd_ind.macd_signal().iloc[-1])
        histogram = float(macd_ind.macd_diff().iloc[-1])

        # Check crossover (current vs previous)
        prev_macd = float(macd_ind.macd().iloc[-2])
        prev_signal = float(macd_ind.macd_signal().iloc[-2])
        crossover = "none"
        if prev_macd <= prev_signal and macd_line > signal_line:
            crossover = "bullish_cross"
        elif prev_macd >= prev_signal and macd_line < signal_line:
            crossover = "bearish_cross"

        diff = macd_line - signal_line
        if diff > 0:
            signal = "bullish"
        elif diff < 0:
            signal = "bearish"
        else:
            signal = "neutral"

        return {
            "signal": signal,
            "macd_value": round(macd_line, 2),
            "signal_value": round(signal_line, 2),
            "histogram": round(histogram, 2),
            "crossover": crossover,
        }
    except Exception as e:
        logger.error(f"Failed to calculate MACD: {e}")
        return None


def calculate_bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> Optional[dict]:
    """
    Calculate Bollinger Bands.

    Returns:
        Dict with keys: upper, middle, lower, bandwidth, percent_b.
        percent_b: 0 = at lower band, 1 = at upper band, 0.5 = middle.
    """
    try:
        bb = ta_lib.volatility.BollingerBands(df["close"], window=period, window_dev=std_dev)
        upper = float(bb.bollinger_hband().iloc[-1])
        middle = float(bb.bollinger_mavg().iloc[-1])
        lower = float(bb.bollinger_lband().iloc[-1])
        bandwidth = (upper - lower) / middle * 100 if middle > 0 else 0
        price = float(df["close"].iloc[-1])
        percent_b = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5

        return {
            "upper": round(upper, 2),
            "middle": round(middle, 2),
            "lower": round(lower, 2),
            "bandwidth": round(bandwidth, 2),
            "percent_b": round(percent_b, 3),
        }
    except Exception as e:
        logger.error(f"Failed to calculate Bollinger Bands: {e}")
        return None


def calculate_emas(df: pd.DataFrame) -> Optional[dict]:
    """
    Calculate EMA 20, 50, 200.

    Returns:
        Dict with keys: ema_20, ema_50, ema_200, trend.
        trend: 'strong_bull' if price > all EMAs in order,
               'strong_bear' if price < all EMAs in order,
               'bull', 'bear', or 'mixed'.
    """
    try:
        price = float(df["close"].iloc[-1])
        ema_20 = float(ta_lib.trend.EMAIndicator(df["close"], window=20).ema_indicator().iloc[-1])
        ema_50 = float(ta_lib.trend.EMAIndicator(df["close"], window=50).ema_indicator().iloc[-1])

        # EMA 200 needs enough data
        if len(df) >= 200:
            ema_200 = float(ta_lib.trend.EMAIndicator(df["close"], window=200).ema_indicator().iloc[-1])
        else:
            ema_200 = None

        # Determine trend
        if ema_200 is not None:
            if price > ema_20 > ema_50 > ema_200:
                trend = "strong_bull"
            elif price < ema_20 < ema_50 < ema_200:
                trend = "strong_bear"
            elif price > ema_50:
                trend = "bull"
            elif price < ema_50:
                trend = "bear"
            else:
                trend = "mixed"
        else:
            if price > ema_20 > ema_50:
                trend = "bull"
            elif price < ema_20 < ema_50:
                trend = "bear"
            else:
                trend = "mixed"

        return {
            "ema_20": round(ema_20, 2),
            "ema_50": round(ema_50, 2),
            "ema_200": round(ema_200, 2) if ema_200 else None,
            "trend": trend,
        }
    except Exception as e:
        logger.error(f"Failed to calculate EMAs: {e}")
        return None


def calculate_atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Calculate Average True Range — measures volatility."""
    try:
        atr = ta_lib.volatility.AverageTrueRange(
            df["high"], df["low"], df["close"], window=period
        ).average_true_range()
        return round(float(atr.iloc[-1]), 2)
    except Exception as e:
        logger.error(f"Failed to calculate ATR: {e}")
        return None


def calculate_volume_profile(df: pd.DataFrame) -> Optional[dict]:
    """
    Analyze volume patterns.

    Returns:
        Dict with volume_sma_ratio (current vs 20-period average),
        volume_trend ('increasing', 'decreasing', 'stable'),
        buy_pressure (taker buy volume / total volume ratio).
    """
    try:
        vol = df["volume"]
        vol_sma = vol.rolling(window=20).mean().iloc[-1]
        current_vol = vol.iloc[-1]
        ratio = current_vol / vol_sma if vol_sma > 0 else 1.0

        # Volume trend over last 5 candles
        recent_vol = vol.iloc[-5:].values
        if recent_vol[-1] > recent_vol[0] * 1.2:
            vol_trend = "increasing"
        elif recent_vol[-1] < recent_vol[0] * 0.8:
            vol_trend = "decreasing"
        else:
            vol_trend = "stable"

        # Buy pressure
        taker_buy = df["taker_buy_base"].astype(float)
        buy_pressure = float(taker_buy.iloc[-1]) / current_vol if current_vol > 0 else 0.5

        return {
            "volume_sma_ratio": round(ratio, 2),
            "volume_trend": vol_trend,
            "buy_pressure": round(buy_pressure, 3),
        }
    except Exception as e:
        logger.error(f"Failed to calculate volume profile: {e}")
        return None


def find_support_resistance(df: pd.DataFrame) -> Optional[dict]:
    """
    Find recent support and resistance levels using pivot points.

    Returns:
        Dict with nearest_support, nearest_resistance, distance_to_support_pct,
        distance_to_resistance_pct.
    """
    try:
        price = float(df["close"].iloc[-1])
        highs = df["high"].iloc[-50:].values
        lows = df["low"].iloc[-50:].values

        # Simple approach: recent swing highs/lows
        resistance_levels = []
        support_levels = []

        for i in range(2, len(highs) - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                resistance_levels.append(float(highs[i]))
            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                support_levels.append(float(lows[i]))

        # Find nearest levels
        supports_below = [s for s in support_levels if s < price]
        resistances_above = [r for r in resistance_levels if r > price]

        nearest_support = max(supports_below) if supports_below else price * 0.97
        nearest_resistance = min(resistances_above) if resistances_above else price * 1.03

        return {
            "nearest_support": round(nearest_support, 2),
            "nearest_resistance": round(nearest_resistance, 2),
            "distance_to_support_pct": round((price - nearest_support) / price * 100, 2),
            "distance_to_resistance_pct": round((nearest_resistance - price) / price * 100, 2),
        }
    except Exception as e:
        logger.error(f"Failed to find S/R levels: {e}")
        return None


# ── 24h Stats ────────────────────────────────────────────────────────────────

def get_24h_stats() -> Optional[dict]:
    """Fetch 24h ticker stats from Binance."""
    try:
        stats = get_binance_client().get_ticker(symbol=config.SYMBOL)
        return {
            "price_change_pct": round(float(stats["priceChangePercent"]), 2),
            "high_24h": round(float(stats["highPrice"]), 2),
            "low_24h": round(float(stats["lowPrice"]), 2),
            "volume_24h": round(float(stats["volume"]), 2),
            "quote_volume_24h": round(float(stats["quoteVolume"]), 2),
        }
    except Exception as e:
        logger.error(f"Failed to fetch 24h stats: {e}")
        return None


# ── Fear & Greed Index ───────────────────────────────────────────────────────

def get_fear_greed_index() -> Optional[int]:
    """Fetch the current Crypto Fear & Greed Index (0-100)."""
    try:
        resp = requests.get(config.FEAR_GREED_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        value = int(data["data"][0]["value"])
        logger.info(f"Fear & Greed Index: {value}")
        return value
    except Exception as e:
        logger.error(f"Failed to fetch Fear & Greed Index: {e}")
        return None


# ── Aggregate All Market Data ────────────────────────────────────────────────

def analyze_timeframe(interval: str, label: str, limit: int = 100) -> dict:
    """Run full technical analysis on a single timeframe."""
    df = get_candles(interval, limit)
    if df is None:
        return {"error": f"Failed to fetch {label} candles"}

    rsi = calculate_rsi(df)
    macd = calculate_macd(df)
    bb = calculate_bollinger_bands(df)
    emas = calculate_emas(df)
    atr = calculate_atr(df)
    volume = calculate_volume_profile(df)
    sr = find_support_resistance(df)

    result = {
        "rsi": rsi,
        "macd": macd or {"signal": "neutral", "crossover": "none", "histogram": 0},
        "bollinger": bb,
        "emas": emas,
        "atr": atr,
        "volume": volume,
        "support_resistance": sr,
    }
    logger.info(f"{label} analysis: RSI={rsi}, MACD={macd['signal'] if macd else 'N/A'}, EMA trend={emas['trend'] if emas else 'N/A'}")
    return result


def fetch_all_market_data() -> Optional[dict]:
    """
    Fetch all market data: price, multi-timeframe indicators, sentiment.
    This is the main function called by the trade cycle.
    """
    price = get_current_price()
    if price is None:
        logger.error("Cannot proceed without BTC price")
        return None

    # Multi-timeframe analysis
    tf_1h = analyze_timeframe(BinanceClient.KLINE_INTERVAL_1HOUR, "1H", 200)
    tf_4h = analyze_timeframe(BinanceClient.KLINE_INTERVAL_4HOUR, "4H", 200)

    # 24h stats
    stats_24h = get_24h_stats()

    # Fear & Greed (keep as sentiment gauge, not for decisions)
    fear_greed = get_fear_greed_index()

    market_data = {
        "btc_price": price,
        "timeframes": {
            "1h": tf_1h,
            "4h": tf_4h,
        },
        "stats_24h": stats_24h or {},
        "fear_greed_index": fear_greed,
        # Backward compat fields
        "rsi_1h": tf_1h.get("rsi"),
        "macd_signal": tf_1h.get("macd", {}).get("signal", "neutral"),
        "volume_change_24h": stats_24h.get("price_change_pct") if stats_24h else None,
    }

    logger.info("All market data fetched successfully")
    return market_data
