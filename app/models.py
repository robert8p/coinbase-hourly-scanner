from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
from typing import Deque, Dict, Optional, Tuple
import time

@dataclass
class ProductMeta:
    product_id: str
    base_currency: str
    quote_currency: str
    status: str

@dataclass
class TickerState:
    # Rolling last ~2h of price points (timestamp seconds, price float)
    prices: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=5000))
    # Rolling last ~2h of trade sizes (timestamp seconds, size float)
    sizes: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=20000))

    last_price: Optional[float] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    volume_24h_base: Optional[float] = None  # as provided by feed, in base units
    last_update: Optional[float] = None

@dataclass
class AppState:
    started_at: float = field(default_factory=lambda: time.time())
    ws_connected: bool = False
    ws_last_msg_at: Optional[float] = None
    ws_last_error: Optional[str] = None
    ws_reconnects: int = 0
    products: Dict[str, ProductMeta] = field(default_factory=dict)
    tickers: Dict[str, TickerState] = field(default_factory=dict)
    tracked_product_ids: list[str] = field(default_factory=list)

    # Simple counters
    ticker_messages: int = 0
    status_messages: int = 0
