# Self-Learning Crypto Trading Bot

A fully automated BTC/USDT trading bot that uses Claude AI as its brain for making trading decisions. The bot learns from its own trade history through nightly self-reviews, getting smarter each day.

## Architecture

```
main.py              → Entry point, starts scheduler
config.py            → All configuration constants
bot/
  market_data.py     → Fetch price, indicators, news
  brain.py           → Claude API decision making
  executor.py        → Binance order execution + safety checks
  memory.py          → Supabase database interface
  reviewer.py        → Nightly self-review logic
migration.sql        → Supabase database schema
```

## How It Works

**Every 4 hours** (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC):
1. Fetches BTC price, RSI, MACD, volume, Fear & Greed Index, and news
2. Builds context from market data + trade history + past lessons
3. Asks Claude for a BUY/SELL/HOLD decision
4. Runs safety checks (confidence >= 7, balance protection, position limits)
5. Executes on Binance if safe, logs everything to Supabase

**Every night at 23:30 UTC**:
- Claude reviews the day's performance
- Writes a lesson with patterns identified and adjustments
- These lessons feed into future trading decisions

## Prerequisites

- Python 3.11+
- Binance account with API key (Spot trading enabled)
- Anthropic API key
- Supabase project (free tier works)
- CryptoPanic API key (free tier, optional but recommended)

## Setup

### 1. Supabase Database

1. Create a new project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** in the dashboard
3. Paste the contents of `migration.sql` and run it
4. Copy your project URL and anon key from **Settings > API**

### 2. Binance API

1. Log into Binance and go to **API Management**
2. Create a new API key with **Spot Trading** permission enabled
3. Restrict IP access for security (recommended)

### 3. Anthropic API

1. Get an API key from [console.anthropic.com](https://console.anthropic.com)

### 4. Telegram Bot

1. Open Telegram, find **@BotFather**
2. Send `/newbot`, follow the prompts
3. Copy the Bot Token
4. Start the bot, send a message, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to get your Chat ID

### 5. Environment Variables

```bash
cp .env.example .env
```

Edit `.env` and fill in all values:

```
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
ANTHROPIC_API_KEY=sk-ant-...
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
INITIAL_CAPITAL=100
MAX_POSITION_PERCENT=0.30
STOP_LOSS_MINIMUM_BALANCE=70
MIN_CONFIDENCE_TO_TRADE=7
DAILY_TARGET_PERCENT=2.0
```

### 6. Install Dependencies

```bash
pip install -r requirements.txt
```

### 7. Run

```bash
# Verify mode — dry run, no real trades, tests full pipeline
python main.py --verify

# Live mode — starts trading for real
python main.py
```

The bot will:
- Run one trade cycle immediately on startup
- Then follow the 4-hour schedule automatically
- Log to both console and `trading-bot.log`
- Send notifications to Telegram

## Safety Features

| Check | Description |
|-------|-------------|
| Min confidence | Only trades when Claude confidence >= 7/10 |
| Balance protection | Stops all trading if total < $70 |
| Daily target | Switches to HOLD-only after +2% daily gain |
| Position limit | Max 30% of USDT per trade |
| BTC cap | Cannot BUY if BTC > 50% of portfolio |
| News alert | Forces HOLD on `high_alert` news |
| API fallback | Defaults to HOLD if Claude/Binance API fails |
| Position isolation | Only sells BTC the bot bought, not user's DCA holdings |

## Deploy to Ubuntu VPS with systemd

### 1. Setup on VPS

```bash
# Install Python
sudo apt update && sudo apt install python3.11 python3.11-venv -y

# Clone or copy the project
cd /opt
git clone <your-repo-url> trading-bot
cd trading-bot

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Setup env
cp .env.example .env
nano .env  # fill in your values
```

### 2. Create systemd Service

```bash
sudo nano /etc/systemd/system/trading-bot.service
```

Paste:

```ini
[Unit]
Description=Self-Learning Crypto Trading Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/trading-bot
ExecStart=/opt/trading-bot/venv/bin/python main.py
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 3. Enable and Start

```bash
sudo systemctl daemon-reload
sudo systemctl enable trading-bot
sudo systemctl start trading-bot
```

### 4. Monitor

```bash
# Check status
sudo systemctl status trading-bot

# View live logs
sudo journalctl -u trading-bot -f

# View bot's own log file
tail -f /opt/trading-bot/trading-bot.log
```

## Disclaimer

This bot is for educational purposes. Cryptocurrency trading involves significant risk. Never trade with money you cannot afford to lose. Past performance does not guarantee future results.
