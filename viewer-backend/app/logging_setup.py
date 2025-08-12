"""Structured logging configuration.

Provides JSON log formatting when ENABLE_JSON_LOGS=1 (default), otherwise a
concise human formatter. Fields: timestamp, level, msg, logger, request_id,
path, method, status, duration_ms.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from time import time


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - formatting
        base = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
        }
        for attr in ("request_id", "path", "method", "status", "duration_ms"):
            if hasattr(record, attr):
                base[attr] = getattr(record, attr)
        if record.exc_info:
            base["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(base, ensure_ascii=False)


class _PlainFormatter(logging.Formatter):  # pragma: no cover - formatting
    def format(self, record: logging.LogRecord) -> str:
        parts = [
            self.formatTime(record, datefmt="%H:%M:%S"),
            record.levelname[0],
            record.name + ":",
            record.getMessage(),
        ]
        if hasattr(record, "request_id"):
            parts.append(f"rid={getattr(record, 'request_id')}")
        if hasattr(record, "path"):
            parts.append(f"path={getattr(record, 'path')}")
        if hasattr(record, "status"):
            parts.append(f"status={getattr(record, 'status')}")
        return " ".join(parts)


def configure_logging() -> None:
    if getattr(configure_logging, "_configured", False):  # idempotent
        return
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    json_enabled = os.getenv("ENABLE_JSON_LOGS", "1") == "1"
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):  # remove uvicorn's default handlers for consistency
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter() if json_enabled else _PlainFormatter())
    root.addHandler(handler)
    configure_logging._configured = True  # type: ignore[attr-defined]


async def logging_middleware(request, call_next):  # pragma: no cover - thin wrapper
    import uuid

    rid = uuid.uuid4().hex[:8]
    start = time()
    request.state.request_id = rid
    logger = logging.getLogger("request")
    logger.info("request.start", extra={"request_id": rid, "path": request.url.path, "method": request.method})
    try:
        response = await call_next(request)
        return response
    finally:
        dur = (time() - start) * 1000.0
        status = getattr(locals().get("response", None), "status_code", None)
        logger.info(
            "request.end",
            extra={
                "request_id": rid,
                "path": request.url.path,
                "method": request.method,
                "status": status,
                "duration_ms": round(dur, 2),
            },
        )
