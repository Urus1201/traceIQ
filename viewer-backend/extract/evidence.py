from __future__ import annotations

from typing import Any, List, Tuple, TypedDict, Optional, cast


class FieldEvidence(TypedDict, total=False):
    value: Any
    confidence: float
    line_refs: List[int]
    raw_spans: List[Tuple[int, int]]  # inclusive [start, end) or [start,end]; consumer-defined


def _to_float(v: Any) -> Optional[float]:
    try:
        if isinstance(v, bool):  # avoid bool as int
            return float(int(v))
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            return float(v)
    except Exception:
        return None
    return None


def _numeric_equal(a: Any, b: Any, tol: float = 1e-9) -> bool:
    fa = _to_float(a)
    fb = _to_float(b)
    if fa is None or fb is None:
        return False
    return abs(fa - fb) <= tol


def make_evidence(value: Any, confidence: float, lineno: int, span: Optional[Tuple[int, int]] = None) -> FieldEvidence:
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("confidence must be in [0,1]")
    ev: FieldEvidence = {
        "value": value,
        "confidence": confidence,
        "line_refs": [int(lineno)],
    }
    if span is not None:
        ev["raw_spans"] = [span]
    return ev


def merge_evidence(pref: FieldEvidence, alt: FieldEvidence) -> FieldEvidence:
    """
    Merge two evidences:
    - Pick the one with higher confidence.
    - If equal confidence and numeric values agree, keep pref (including its value).
    - Always union line_refs and raw_spans (deduplicated, sorted).
    """
    c1 = cast(float, pref.get("confidence", 0.0))
    c2 = cast(float, alt.get("confidence", 0.0))

    # Determine winner for value/confidence
    if c2 > c1:
        winner = alt
    elif c1 > c2:
        winner = pref
    else:
        # Equal confidence
        if _numeric_equal(pref.get("value"), alt.get("value")):
            winner = pref
        else:
            winner = pref  # tie-breaker: prefer pref

    merged: FieldEvidence = {
        "value": winner.get("value"),
        "confidence": cast(float, winner.get("confidence", 0.0)),
    }

    # Union line_refs
    l1 = list(cast(List[int], pref.get("line_refs", [])))
    l2 = list(cast(List[int], alt.get("line_refs", [])))
    line_refs = sorted({int(x) for x in l1 + l2})
    if line_refs:
        merged["line_refs"] = line_refs

    # Union raw_spans if present
    s1 = list(cast(List[Tuple[int, int]], pref.get("raw_spans", [])))
    s2 = list(cast(List[Tuple[int, int]], alt.get("raw_spans", [])))
    if s1 or s2:
        # Deduplicate tuples
        merged["raw_spans"] = sorted(set(s1 + s2))

    return merged
