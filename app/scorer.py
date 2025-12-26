from __future__ import annotations

import math
import time
from typing import Dict, List, Optional, Tuple

from .models import AppState, TickerState

STABLE_BASES = {"USDC", "USDT", "DAI", "EURC", "TUSD", "USDP"}

def _find_price_at_or_before(series: List[Tuple[float, float]], target_ts: float) -> Optional[float]:
    # series is sorted by time ascending
    # We scan from the end (most recent) backwards to find first <= target
    for ts, px in reversed(series):
        if ts <= target_ts:
            return px
    return None

def _sum_sizes_after(series: List[Tuple[float, float]], cutoff_ts: float) -> float:
    s = 0.0
    for ts, size in series:
        if ts >= cutoff_ts:
            s += size
    return s

def score_opportunities(
    state: AppState,
    horizon_minutes: int = 60,
    limit: int = 10,
    min_quote_vol_usd_24h: float = 5_000_000,
    max_spread_pct: float = 0.006,
) -> Dict:
    now = time.time()
    items = []

    # Warm-up heuristic: if we started < 20m ago, most pairs won't have good stats.
    uptime = now - state.started_at
    warmup = "warming_up" if uptime < 20 * 60 else ("partial" if uptime < 75 * 60 else "ready")

    for pid in state.tracked_product_ids:
        t: TickerState = state.tickers.get(pid)  # type: ignore
        if not t or t.last_price is None or t.last_update is None:
            continue

        # Copy deques to lists to avoid holding references during iteration
        prices = list(t.prices)
        sizes = list(t.sizes)

        # Basic sanity
        if len(prices) < 10:
            continue

        price_now = t.last_price
        ts_now = prices[-1][0]

        p15 = _find_price_at_or_before(prices, ts_now - 15 * 60)
        p60 = _find_price_at_or_before(prices, ts_now - 60 * 60)

        ret_15m = (price_now / p15 - 1.0) if p15 else None
        ret_60m = (price_now / p60 - 1.0) if p60 else None

        # Volume anomaly: last 5m volume vs average 5m volume over last 60m
        vol_5m = _sum_sizes_after(sizes, ts_now - 5 * 60)
        vol_60m = _sum_sizes_after(sizes, ts_now - 60 * 60)
        vol_anom = (vol_5m / (vol_60m / 12.0)) if vol_60m > 0 else None  # 12 five-minute blocks per hour

        # Spread
        spread_pct = None
        if t.best_bid and t.best_ask and t.best_ask > 0 and t.best_bid > 0:
            mid = (t.best_ask + t.best_bid) / 2.0
            if mid > 0:
                spread_pct = (t.best_ask - t.best_bid) / mid

        # Approx 24h quote volume in USD
        quote_vol_usd_24h = None
        if t.volume_24h_base is not None:
            quote_vol_usd_24h = t.volume_24h_base * price_now

        flags = []
        if spread_pct is not None and spread_pct > max_spread_pct:
            flags.append("WIDE_SPREAD")
        if quote_vol_usd_24h is not None and quote_vol_usd_24h < min_quote_vol_usd_24h:
            flags.append("LOW_LIQUIDITY")
        if ret_60m is None:
            flags.append("NO_60M_HISTORY")
        if vol_anom is None:
            flags.append("NO_VOL_HISTORY")

        # Gating: ignore very wide spreads, or very low liquidity (unless we're still warming)
        if warmup == "ready":
            if spread_pct is not None and spread_pct > max_spread_pct:
                continue
            if quote_vol_usd_24h is not None and quote_vol_usd_24h < min_quote_vol_usd_24h:
                continue

        # Score: simple heuristic (momentum + flow - spread penalty)
        # Use small eps to avoid None or math errors.
        r15 = ret_15m if ret_15m is not None else 0.0
        r60 = ret_60m if ret_60m is not None else 0.0
        va = vol_anom if vol_anom is not None else 1.0
        sp = spread_pct if spread_pct is not None else 0.0

        score = (0.60 * r15) + (0.40 * r60) + (0.08 * math.log1p(max(0.0, va - 1.0))) - (3.0 * sp)

        drivers = []
        if ret_15m is not None:
            drivers.append(f"15m momentum {ret_15m*100:+.2f}%")
        if ret_60m is not None:
            drivers.append(f"60m momentum {ret_60m*100:+.2f}%")
        if vol_anom is not None:
            drivers.append(f"5m volume {vol_anom:.2f}× vs 60m avg")
        if spread_pct is not None:
            drivers.append(f"Spread {spread_pct*100:.3f}%")
        if quote_vol_usd_24h is not None:
            drivers.append(f"24h $vol ~{quote_vol_usd_24h:,.0f}")

        items.append({
            "product_id": pid,
            "price": round(price_now, 10),
            "ret_15m": ret_15m,
            "ret_60m": ret_60m,
            "vol_anom": vol_anom,
            "spread_pct": spread_pct,
            "quote_vol_usd_24h": quote_vol_usd_24h,
            "score": score,
            "flags": flags,
            "drivers": drivers[:5],
        })

    items.sort(key=lambda x: x["score"], reverse=True)
    top = items[: max(1, limit)]

    note = ""
    if not top:
        note = "No opportunities yet — waiting for ticker data (this is normal right after deploy)."
    elif warmup != "ready":
        note = "Warm-up mode: rankings are based on limited history; expect instability for ~60–90 minutes."

    return {
        "asof": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now)),
        "horizon_minutes": horizon_minutes,
        "opportunities": top,
        "note": note,
        "meta": {
            "ws_connected": state.ws_connected,
            "last_msg_seconds_ago": None if state.ws_last_msg_at is None else round(now - state.ws_last_msg_at, 1),
            "tracked_products": len(state.tracked_product_ids),
            "ticker_messages": state.ticker_messages,
            "status_messages": state.status_messages,
            "warmup": warmup,
            "uptime_minutes": round((now - state.started_at) / 60.0, 1),
        },
    }
