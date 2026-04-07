-- ============================================================================
-- Supabase Migration: Self-Learning Crypto Trading Bot
-- Run this SQL in your Supabase SQL Editor to create all required tables.
-- ============================================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── 1. Trades Table ─────────────────────────────────────────────────────────
-- Stores every trading decision (BUY, SELL, HOLD) with Claude's reasoning.
CREATE TABLE IF NOT EXISTS trades (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    action          TEXT NOT NULL CHECK (action IN ('BUY', 'SELL', 'HOLD')),
    symbol          TEXT DEFAULT 'BTCUSDT',
    price_at_decision FLOAT NOT NULL,
    quantity_usdt   FLOAT DEFAULT 0,
    reasoning       TEXT,
    confidence      INT CHECK (confidence >= 1 AND confidence <= 10),
    suggested_stop_loss FLOAT,
    status          TEXT DEFAULT 'open' CHECK (status IN ('open', 'closed', 'stopped')),
    exit_price      FLOAT,
    pnl_usdt        FLOAT,
    pnl_percent     FLOAT
);

-- Index for querying open trades and recent trades
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades (status);
CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades (created_at DESC);

-- ── 2. Daily Lessons Table ──────────────────────────────────────────────────
-- Stores Claude's nightly self-review reflections for learning over time.
CREATE TABLE IF NOT EXISTS daily_lessons (
    id                  UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    date                DATE NOT NULL UNIQUE,
    total_trades        INT DEFAULT 0,
    wins                INT DEFAULT 0,
    losses              INT DEFAULT 0,
    lesson_text         TEXT,
    pattern_identified  TEXT,
    adjustment_made     TEXT
);

-- Index for querying recent lessons
CREATE INDEX IF NOT EXISTS idx_daily_lessons_date ON daily_lessons (date DESC);

-- ── 3. Account Snapshots Table ──────────────────────────────────────────────
-- Periodic snapshots of the account balance for tracking portfolio value.
CREATE TABLE IF NOT EXISTS account_snapshots (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    usdt_balance    FLOAT NOT NULL,
    btc_balance     FLOAT NOT NULL,
    total_value_usdt FLOAT NOT NULL,
    daily_pnl_percent FLOAT
);

-- Index for querying latest snapshot
CREATE INDEX IF NOT EXISTS idx_snapshots_created_at ON account_snapshots (created_at DESC);

-- ── 4. Market Contexts Table ────────────────────────────────────────────────
-- Stores market data snapshots taken at each trade cycle for analysis.
CREATE TABLE IF NOT EXISTS market_contexts (
    id                  UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    btc_price           FLOAT NOT NULL,
    rsi_1h              FLOAT,
    macd_signal         TEXT CHECK (macd_signal IN ('bullish', 'bearish', 'neutral')),
    fear_greed_index    INT,
    top_news_headlines  TEXT,
    news_sentiment      TEXT CHECK (news_sentiment IN ('positive', 'negative', 'neutral'))
);

-- Index for querying recent market contexts
CREATE INDEX IF NOT EXISTS idx_market_contexts_created_at ON market_contexts (created_at DESC);

-- ── Row Level Security (optional but recommended) ───────────────────────────
-- Enable RLS on all tables. By default, service_role key bypasses RLS.
-- If using anon key, you'll need to add policies.
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_lessons ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_contexts ENABLE ROW LEVEL SECURITY;

-- Allow all operations for authenticated users (adjust as needed)
CREATE POLICY "Allow all for authenticated" ON trades
    FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for authenticated" ON daily_lessons
    FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for authenticated" ON account_snapshots
    FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for authenticated" ON market_contexts
    FOR ALL USING (true) WITH CHECK (true);
