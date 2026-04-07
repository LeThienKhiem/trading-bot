"""
Market data fetcher for the trading bot.
Collects BTC price, technical indicators (RSI, MACD), volume changes,
Fear & Greed Index, and CryptoPanic news headlines.
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


# ── Price & Indicators ───────────────────────────────────────────────────────

def get_current_price() -> Optional[float]:
    """
    Fetch the current BTC/USDT price from Binance.

    Returns:
        Current price as float, or None on failure.
    """
    try:
        ticker = get_binance_client().get_symbol_ticker(symbol=config.SYMBOL)
        price = float(ticker["price"])
        logger.info(f"Current BTC price: ${price:,.2f}")
        return price
    except Exception as e:
        logger.error(f"Failed to fetch BTC price: {e}")
        return None


def get_1h_candles(limit: int = 100) -> Optional[pd.DataFrame]:
    """
    Fetch 1-hour candles for BTC/USDT from Binance.

    Args:
        limit: Number of candles to fetch (default 100).

    Returns:
        DataFrame with columns [open, high, low, close, volume], or None.
    """
    try:
        klines = get_binance_client().get_klines(
            symbol=config.SYMBOL,
            interval=BinanceClient.KLINE_INTERVAL_1HOUR,
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
        logger.error(f"Failed to fetch 1h candles: {e}")
        return None


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """
    Calculate RSI(14) from a candle DataFrame.

    Args:
        df: DataFrame with a 'close' column.
        period: RSI period (default 14).

    Returns:
        Latest RSI value as float, or None on failure.
    """
    try:
        rsi = ta_lib.momentum.RSIIndicator(df["close"], window=period).rsi()
        latest_rsi = round(float(rsi.iloc[-1]), 2)
        logger.info(f"RSI({period}): {latest_rsi}")
        return latest_rsi
    except Exception as e:
        logger.error(f"Failed to calculate RSI: {e}")
        return None


def calculate_macd(df: pd.DataFrame) -> Optional[str]:
    """
    Calculate MACD on a candle DataFrame and return signal direction.

    Returns:
        'bullish' if MACD > signal line, 'bearish' if below, 'neutral'
        if they're roughly equal. Returns None on failure.
    """
    try:
        macd_indicator = ta_lib.trend.MACD(df["close"])
        macd_line = float(macd_indicator.macd().iloc[-1])
        signal_line = float(macd_indicator.macd_signal().iloc[-1])

        diff = macd_line - signal_line
        if diff > 0:
            signal = "bullish"
        elif diff < 0:
            signal = "bearish"
        else:
            signal = "neutral"

        logger.info(f"MACD signal: {signal} (diff={diff:.4f})")
        return signal
    except Exception as e:
        logger.error(f"Failed to calculate MACD: {e}")
        return None


def get_24h_volume_change() -> Optional[float]:
    """
    Fetch the 24h price change percent from Binance ticker stats.

    Returns:
        24h price change percent as float, or None on failure.
    """
    try:
        stats = get_binance_client().get_ticker(symbol=config.SYMBOL)
        change = float(stats["priceChangePercent"])
        logger.info(f"24h price change: {change:.2f}%")
        return round(change, 2)
    except Exception as e:
        logger.error(f"Failed to fetch 24h volume change: {e}")
        return None


# ── Fear & Greed Index ───────────────────────────────────────────────────────

def get_fear_greed_index() -> Optional[int]:
    """
    Fetch the current Crypto Fear & Greed Index from alternative.me.

    Returns:
        Index value (0-100) as int, or None on failure.
        0 = Extreme Fear, 100 = Extreme Greed.
    """
    try:
        resp = requests.get(config.FEAR_GREED_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        value = int(data["data"][0]["value"])
        classification = data["data"][0]["value_classification"]
        logger.info(f"Fear & Greed Index: {value} ({classification})")
        return value
    except Exception as e:
        logger.error(f"Failed to fetch Fear & Greed Index: {e}")
        return None


# ── Crypto News (CoinGecko Trending — free, no key) ─────────────────────────

def get_crypto_news() -> tuple[str, str]:
    """
    Fetch trending coins and market buzz from CoinGecko (free API).
    Uses trending data + Fear & Greed to infer sentiment since CryptoPanic
    free tier was discontinued in April 2026.

    Returns:
        Tuple of (headlines_text, sentiment).
        - headlines_text: formatted string of trending crypto topics
        - sentiment: 'positive', 'negative', or 'neutral'
        Returns ('No news available', 'neutral') on failure.
    """
    try:
        # Fetch trending coins from CoinGecko
        resp = requests.get(config.COINGECKO_BTC_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        coins = data.get("coins", [])[:5]
        headlines = []
        btc_trending = False

        for item in coins:
            coin = item.get("item", {})
            name = coin.get("name", "Unknown")
            symbol = coin.get("symbol", "")
            market_cap_rank = coin.get("market_cap_rank", "N/A")
            price_change_24h = coin.get("data", {}).get(
                "price_change_percentage_24h", {}
            ).get("usd", 0)

            direction = "up" if price_change_24h > 0 else "down"
            headlines.append(
                f"- {name} ({symbol}) trending — rank #{market_cap_rank}, "
                f"24h {direction} {abs(price_change_24h):.1f}%"
            )
            if symbol.upper() == "BTC":
                btc_trending = True

        # Also fetch BTC-specific market data for sentiment
        try:
            btc_resp = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": "bitcoin",
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                },
                timeout=10,
            )
            btc_resp.raise_for_status()
            btc_data = btc_resp.json().get("bitcoin", {})
            btc_24h_change = btc_data.get("usd_24h_change", 0)

            headlines.insert(0, f"- BTC 24h change: {btc_24h_change:+.2f}%")

            # Derive sentiment from BTC price movement
            if btc_24h_change > 3:
                sentiment = "positive"
            elif btc_24h_change < -3:
                sentiment = "negative"
            else:
                sentiment = "neutral"
        except Exception:
            sentiment = "neutral"

        if not headlines:
            return "No trending crypto data available", "neutral"

        headlines_text = "\n".join(headlines)
        logger.info(f"Crypto news sentiment: {sentiment} ({len(headlines)} items)")
        return headlines_text, sentiment

    except Exception as e:
        logger.error(f"Failed to fetch crypto news: {e}")
        return "No news available", "neutral"


# ── Aggregate All Market Data ────────────────────────────────────────────────

def fetch_all_market_data() -> Optional[dict]:
    """
    Fetch all market data in one call: price, indicators, sentiment, news.
    This is the main function called by the trade cycle.

    Returns:
        Dict with all market data fields, or None if critical data
        (price) is unavailable.
    """
    price = get_current_price()
    if price is None:
        logger.error("Cannot proceed without BTC price")
        return None

    candles = get_1h_candles()
    rsi = calculate_rsi(candles) if candles is not None else None
    macd = calculate_macd(candles) if candles is not None else None
    volume_change = get_24h_volume_change()
    fear_greed = get_fear_greed_index()
    headlines, news_sentiment = get_crypto_news()

    market_data = {
        "btc_price": price,
        "rsi_1h": rsi,
        "macd_signal": macd or "neutral",
        "volume_change_24h": volume_change,
        "fear_greed_index": fear_greed,
        "top_news_headlines": headlines,
        "news_sentiment": news_sentiment,
    }

    logger.info("All market data fetched successfully")
    return market_data
