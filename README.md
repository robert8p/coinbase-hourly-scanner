# Coinbase Hourly Scanner (no-code deploy)

A lightweight web app that:
- connects to Coinbase Exchange Market Data WebSocket
- subscribes to `status` (to discover products) and `ticker_batch` (for frequent ticks)
- ranks the top 10 candidates for the next hour using a simple heuristic score

## What it is / isn’t
- ✅ A practical *scanner* for near-term candidates, with liquidity/spread flags
- ❌ Not a guarantee of performance; one-hour moves are noisy

## Configuration (optional)
Environment variables:
- `QUOTE_CCY` (default `USD`)
- `MAX_PRODUCTS` (default `300`) – cap how many pairs to track
- `MIN_QUOTE_VOL_USD_24H` (default `5000000`) – approximate 24h $ volume filter
- `MAX_SPREAD_PCT` (default `0.006`) – 0.6% max spread filter
- `WS_URL` (default `wss://ws-feed.exchange.coinbase.com`)

## Run locally (only if you want)
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 10000
```

Open http://localhost:10000
