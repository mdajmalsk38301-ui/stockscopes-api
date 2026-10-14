"""
Tiny in-memory cache shared by the StockScopes API files.

Why this exists: some endpoints (IPO list, corporate actions) call other
websites every time someone opens a page. That is slow on Render's free
tier. This file lets an endpoint remember its last good answer for a
few minutes and reuse it.

Usage:
    from cache_util import cache_get, cache_set

    saved = cache_get("my_key")
    if saved is not None:
        return jsonify(saved)
    ...build the answer...
    cache_set("my_key", answer, 15 * 60)   # remember for 15 minutes
"""

import time
import threading

_store = {}
_lock = threading.Lock()


def cache_get(key):
    """Return the saved answer for `key`, or None if missing or expired."""
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        value, saved_at, ttl_seconds = entry
        if time.time() - saved_at > ttl_seconds:
            del _store[key]
            return None
        return value


def cache_set(key, value, ttl_seconds):
    """Save `value` under `key` for `ttl_seconds` seconds."""
    with _lock:
        _store[key] = (value, time.time(), ttl_seconds)
