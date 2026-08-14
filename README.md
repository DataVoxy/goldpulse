# GoldPulse — Automated Gold Market Dashboard & Signals

Live gold market dashboard with real-time charts, technical analysis, and algorithmic trading signals.

🔗 **Live:** [goldpulse.datavoxy.com](https://goldpulse.datavoxy.com)
📱 **Signals:** [Telegram Channel](https://t.me/goldpulse14)

## What it does

- Real-time XAU/USD dashboard with TradingView charts
- Algorithmic trading signals (RSI, MACD, ATR-based stops)
- Economic calendar with high-impact event highlighting
- Automated daily market reports
- Trade tracking with P&L logging

## Tech Stack

- **Backend:** Python, pandas, yfinance
- **Hosting:** Cloudflare Workers + R2 (free tier)
- **Signal delivery:** Telegram Bot API
- **Frontend:** Static HTML, TradingView widgets
- **Scheduling:** Windows Task Scheduler

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  yfinance   │────▶│  Python Bot  │────▶│  Telegram API   │
│  (market    │     │  (strategy   │     │  (signals to    │
│   data)     │     │   + analysis)│     │   channel)      │
└─────────────┘     └──────┬───────┘     └─────────────────┘
                           │
                           ▼
                    ┌──────────────┐     ┌─────────────────┐
                    │  JSON data   │────▶│  Cloudflare R2  │
                    │  (dashboard  │     │  + Worker       │
                    │   updates)   │     │  (serves site)  │
                    └──────────────┘     └─────────────────┘
```

## Products

| Product | URL | Status |
|---------|-----|--------|
| GoldPulse | goldpulse.datavoxy.com | ✅ Live |
| SilverPulse | silverpulse.datavoxy.com | ✅ Live |
| CryptoPulse | cryptopulse.datavoxy.com | ✅ Live |

## Algorithm Overview

The trading algorithm uses a trend-following approach with mean-reversion entries:

1. **Trend filter:** EMA 50 determines direction
2. **Entry:** RSI pullback near support/resistance
3. **Risk management:** ATR-based stop-loss (1.2x) and take-profit (2.0x)
4. **Session filter:** Only London & US sessions
5. **News filter:** Blocks entries near FOMC, NFP, CPI
6. **Time management:** Hard 4-hour force-close on all positions

Backtested over 6 months: 341 trades, 51% win rate, robust without outliers.

## Infrastructure Cost

€0/month. Everything runs on free tiers:
- Cloudflare Workers (100k requests/day free)
- Cloudflare R2 (10GB free)
- Telegram Bot API (free)
- yfinance (free)
- GoatCounter analytics (free)

## Author

Built by Tommy — solo developer at [DataVoxy](https://datavoxy.com).
