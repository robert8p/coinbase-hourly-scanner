from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .models import AppState
from .ws_client import run_ws_loop
from .scorer import score_opportunities

WS_URL = os.getenv("WS_URL", "wss://ws-feed.exchange.coinbase.com")
QUOTE_CCY = os.getenv("QUOTE_CCY", "USD").upper()
MAX_PRODUCTS = int(os.getenv("MAX_PRODUCTS", "300"))
MIN_QUOTE_VOL_USD_24H = float(os.getenv("MIN_QUOTE_VOL_USD_24H", "5000000"))
MAX_SPREAD_PCT = float(os.getenv("MAX_SPREAD_PCT", "0.006"))

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Coinbase Hourly Scanner", version="1.0.1")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

STATE = AppState()

@app.on_event("startup")
async def _startup():
    # Start the websocket listener as a background task.
    asyncio.create_task(run_ws_loop(
        state=STATE,
        ws_url=WS_URL,
        quote_ccy=QUOTE_CCY,
        max_products=MAX_PRODUCTS,
    ))

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

@app.get("/api/status")
async def api_status():
    import time
    now = time.time()
    return {
        "ok": True,
        "ws_connected": STATE.ws_connected,
        "ws_last_error": STATE.ws_last_error,
        "ws_reconnects": STATE.ws_reconnects,
        "last_msg_seconds_ago": None if STATE.ws_last_msg_at is None else round(now - STATE.ws_last_msg_at, 1),
        "tracked_products": len(STATE.tracked_product_ids),
        "ticker_messages": STATE.ticker_messages,
        "status_messages": STATE.status_messages,
        "uptime_seconds": round(now - STATE.started_at, 1),
        "ws_url": WS_URL,
        "quote_ccy": QUOTE_CCY,
        "max_products": MAX_PRODUCTS,
    }

@app.get("/api/opportunities")
async def api_opportunities(horizon: int = 60, limit: int = 10):
    # Horizon is accepted for future extension; MVP uses 15m/60m momentum regardless.
    return score_opportunities(
        state=STATE,
        horizon_minutes=horizon,
        limit=limit,
        min_quote_vol_usd_24h=MIN_QUOTE_VOL_USD_24H,
        max_spread_pct=MAX_SPREAD_PCT,
    )


@app.head("/")
async def head_root():
    return Response(status_code=200)

@app.head("/api/status")
async def head_status():
    return Response(status_code=200)

@app.head("/api/opportunities")
async def head_opportunities():
    return Response(status_code=200)
