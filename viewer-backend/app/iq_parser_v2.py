"""Deprecated stub: iq_parser_v2 has been merged into iq_parser.py.

This file remains only to avoid import errors from any stale references.
Use app.iq_parser.parse_header_iq instead. Will be removed in a future cleanup.
"""
from __future__ import annotations

from app.iq_parser import parse_header_iq  # re-export unified implementation

__all__ = ["parse_header_iq"]
