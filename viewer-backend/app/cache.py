from __future__ import annotations
"""Lightweight Redis-backed JSON cache with graceful no-op fallback.

Usage:
    from app.cache import build_cache_from_env
    cache = await build_cache_from_env()
    await cache.set_json("key", {"a": 1})
    data = await cache.get_json("key")

All operations swallow connection errors so the application continues to
function if Redis is unavailable (tests, CI, local runs without the
container). Keys are automatically prefixed.
"""
import os
import json
import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:  # redis-py >= 4 provides asyncio under redis.asyncio
    import redis.asyncio as redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None  # type: ignore

class _NoopCache:
    async def get_json(self, key: str):  # pragma: no cover - trivial
        return None
    async def set_json(self, key: str, value: Any, ttl: int | None = None):  # pragma: no cover - trivial
        return False
    async def close(self):  # pragma: no cover - trivial
        return None

class RedisCache:
    def __init__(self, client: Any, prefix: str = "cache", default_ttl: int = 3600):
        self.client = client
        self.prefix = prefix.rstrip(":")
        self.default_ttl = default_ttl
    def _k(self, key: str) -> str:
        return f"{self.prefix}:{key}" if self.prefix else key
    async def get_json(self, key: str) -> Optional[Any]:
        try:
            raw = await self.client.get(self._k(key))
            if raw is None:
                return None
            try:
                return json.loads(raw)
            except Exception:
                return None
        except Exception as e:  # pragma: no cover (network issues)
            logger.debug("Redis get failed for %s: %s", key, e)
            return None
    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> bool:
        try:
            data = json.dumps(value, separators=(",", ":"))
            ex = ttl if ttl is not None else self.default_ttl
            await self.client.set(self._k(key), data, ex=ex)
            return True
        except Exception as e:  # pragma: no cover
            logger.debug("Redis set failed for %s: %s", key, e)
            return False
    async def close(self):  # pragma: no cover - rarely used
        try:
            await self.client.close()
        except Exception:
            pass

async def build_cache_from_env() -> RedisCache | _NoopCache:
    """Instantiate a RedisCache if REDIS_URL is set and reachable; else a no-op.

    Env vars:
      REDIS_URL          e.g. redis://redis:6379/0
      CACHE_DISABLE=1    force disable
      CACHE_PREFIX       (optional) namespace prefix (default 'header')
      CACHE_TTL_SECONDS  (optional) default TTL (int, default 3600)
    """
    if os.getenv("CACHE_DISABLE") == "1":
        return _NoopCache()
    url = os.getenv("REDIS_URL")
    if not url or redis is None:  # redis library not present or env missing
        return _NoopCache()
    try:
        client = redis.from_url(url, encoding="utf-8", decode_responses=False)  # type: ignore
        # Ping with short timeout so startup isn't delayed badly
        await asyncio.wait_for(client.ping(), timeout=0.75)
        prefix = os.getenv("CACHE_PREFIX", "header")
        ttl = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
        return RedisCache(client, prefix=prefix, default_ttl=ttl)
    except Exception as e:  # pragma: no cover (network issues)
        logger.info("Redis unavailable (%s); proceeding without cache", e)
        return _NoopCache()
