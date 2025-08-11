from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List

from .heuristics import MatchedToken


def pack_matched(tokens: List[MatchedToken]) -> List[Dict[str, Any]]:
    out = []
    for t in tokens:
        d = asdict(t)
        # Rename for API shape
        d.pop("span", None)
        d.pop("source", None)
        out.append({
            "token": d["token"],
            "weight": d["weight"],
            "source_line": d["line_idx"],
        })
    return out


__all__ = ["pack_matched"]
