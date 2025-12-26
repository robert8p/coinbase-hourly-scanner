from __future__ import annotations

import asyncio
import json
import time
from typing import Dict, List, Optional

import websockets
from websockets import WebSocketClientProtocol

from .models import AppState, ProductMeta, TickerState

STABLE_BASES = {"USDC", "USDT", "DAI", "EURC", "TUSD", "USDP"}

def _safe_float(x) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

def _chunk(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]

async def run_ws_loop(
    state: AppState,
    ws_url: str,
    quote_ccy: str,
    max_products: int,
    subscribe_chunk_size: int = 100,
):
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                state.ws_connected = True
                state.ws_last_error = None
                backoff = 1.0

                # 1) Subscribe to status (gives us the full product list on interval)
                # Per docs, you must subscribe within 5 seconds or you're disconnected.
                await ws.send(json.dumps({
                    "type": "subscribe",
                    "channels": [{"name": "status"}]
                }))

                # Wait for first status message so we know what products exist
                product_ids = await _await_first_status_and_select_products(state, ws, quote_ccy, max_products)

                # 2) Subscribe to ticker_batch for selected products, in chunks.
                # Use ticker_batch for frequent updates and reduced bandwidth.
                chunks = _chunk(product_ids, subscribe_chunk_size)
                for ch in chunks:
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "product_ids": ch,
                        "channels": ["ticker_batch"]
                    }))
                    # small delay to avoid spamming subscribe messages
                    await asyncio.sleep(0.25)

                # 3) Listen forever
                async for msg in ws:
                    state.ws_last_msg_at = time.time()
                    _handle_message(state, msg)

        except Exception as e:
            state.ws_connected = False
            state.ws_reconnects += 1
            state.ws_last_error = f"{type(e).__name__}: {e}"
            print(f"WS error: {state.ws_last_error}", flush=True)
            # Reconnect with exponential backoff (capped)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.8, 30.0)

async def _await_first_status_and_select_products(
    state: AppState,
    ws: WebSocketClientProtocol,
    quote_ccy: str,
    max_products: int,
) -> List[str]:
    # We may receive non-status messages first; loop until we get status.
    deadline = time.time() + 20
    selected: List[str] = []
    while time.time() < deadline:
        raw = await ws.recv()
        state.ws_last_msg_at = time.time()
        _handle_message(state, raw)

        try:
            data = json.loads(raw)
        except Exception:
            continue
        if data.get("type") != "status":
            continue
        products = data.get("products") or []
        for p in products:
            pid = p.get("id")
            base = p.get("base_currency")
            quote = p.get("quote_currency")
            status = p.get("status")
            if not pid or not base or not quote:
                continue
            # Keep metadata
            state.products[pid] = ProductMeta(
                product_id=pid,
                base_currency=base,
                quote_currency=quote,
                status=status or "",
            )

        # Selection: online spot pairs quoted in quote_ccy, excluding stable bases.
        # Heuristic: prefer *-USD pairs (or chosen quote_ccy).
        candidates = [
            pid for pid, meta in state.products.items()
            if meta.quote_currency == quote_ccy
            and (meta.status or "").lower() == "online"
            and meta.base_currency not in STABLE_BASES
            and pid.endswith(f"-{quote_ccy}")
        ]
        candidates.sort()  # stable order; can be replaced later with volume-based once tickers arrive
        selected = candidates[:max_products]
        state.tracked_product_ids = selected
        # Ensure ticker state exists
        for pid in selected:
            state.tickers.setdefault(pid, TickerState())
        return selected

    # If we didn't receive status in time, fall back to a minimal set
    fallback = [f"BTC-{quote_ccy}", f"ETH-{quote_ccy}"]
    state.tracked_product_ids = fallback
    for pid in fallback:
        state.tickers.setdefault(pid, TickerState())
    return fallback

def _handle_message(state: AppState, raw: str):
    try:
        data = json.loads(raw)
    except Exception:
        return

    mtype = data.get("type")
    if mtype == "status":
        state.status_messages += 1
        return

    if mtype in ("ticker", "ticker_batch"):
        state.ticker_messages += 1
        pid = data.get("product_id")
        if not pid:
            return

        t = state.tickers.get(pid)
        if t is None:
            # ignore tickers we didn't subscribe to
            return

        price = _safe_float(data.get("price"))
        best_bid = _safe_float(data.get("best_bid"))
        best_ask = _safe_float(data.get("best_ask"))
        volume_24h = _safe_float(data.get("volume_24h"))
        last_size = _safe_float(data.get("last_size"))

        ts = _parse_time_to_epoch(data.get("time")) or time.time()

        if price is not None:
            t.last_price = price
            t.prices.append((ts, price))

        if best_bid is not None:
            t.best_bid = best_bid
        if best_ask is not None:
            t.best_ask = best_ask
        if volume_24h is not None:
            t.volume_24h_base = volume_24h
        if last_size is not None:
            t.sizes.append((ts, last_size))

        t.last_update = time.time()

        # prune old points occasionally
        _prune(t, older_than_seconds=2 * 60 * 60)

def _parse_time_to_epoch(t) -> Optional[float]:
    # Coinbase Exchange WS times are ISO8601 strings, e.g. "2022-10-19T23:28:22.061769Z"
    if not isinstance(t, str):
        return None
    try:
        # Very small parser to avoid extra dependencies:
        # Use datetime from stdlib.
        from datetime import datetime, timezone
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None

def _prune(t: TickerState, older_than_seconds: int):
    cutoff = time.time() - older_than_seconds
    # deques are time-ordered, so pop left until in range
    while t.prices and t.prices[0][0] < cutoff:
        t.prices.popleft()
    while t.sizes and t.sizes[0][0] < cutoff:
        t.sizes.popleft()
