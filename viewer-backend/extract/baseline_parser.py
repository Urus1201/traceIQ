from __future__ import annotations

import re
from typing import Dict, List, Tuple, Optional

from app.schemas import FieldEvidence
from extract.value_extractors import (
    match_sample_interval_ms,
    match_samples_per_trace,
)


def _search_lines(lines: List[str], patterns: List[re.Pattern[str]]) -> Optional[Tuple[int, re.Match[str]]]:
    for i, line in enumerate(lines):
        text = line
        for p in patterns:
            m = p.search(text)
            if m:
                return (i + 1, m)
    return None


def _maybe_ms(value: float) -> float:
    """Normalize a unitless sample interval value to milliseconds.

    Heuristic:
    - 100..10000  -> microseconds (divide by 1000)
    - < 50        -> milliseconds
    - otherwise   -> assume milliseconds
    """
    if 100 <= value <= 10000:
        return value / 1000.0
    if value < 50:
        return value
    return value


def parse_baseline(lines: List[str]) -> Dict[str, FieldEvidence]:
    """Deterministic baseline extraction from textual header.

    Returns a dict[field_name] -> FieldEvidence. Always includes line_refs when present.
    """
    out: Dict[str, FieldEvidence] = {}

    # ------------------------------
    # Numeric: sample_interval_ms
    # ------------------------------
    # Priority 1: explicit "... MS"
    for idx, line in enumerate(lines):
        v = match_sample_interval_ms(line)
        if v:
            out["sample_interval_ms"] = FieldEvidence(
                value=float(v[0]), confidence=0.9, line_refs=[idx + 1]
            )
            break

    # Priority 2: unitless number with heuristic normalization (common: microseconds 2000/4000)
    if "sample_interval_ms" not in out:
        for idx, line in enumerate(lines):
            m = re.search(
                r"SAMPLE\s+INTER\w*\s*[:=]?\s*([0-9]*\.?[0-9]+)(?:\s|$)",
                line,
                re.IGNORECASE,
            )
            if m:
                raw = float(m.group(1))
                val = _maybe_ms(raw)
                conf = 0.85 if 100 <= raw <= 10000 else 0.7
                out["sample_interval_ms"] = FieldEvidence(
                    value=val, confidence=conf, line_refs=[idx + 1]
                )
                break

    # ------------------------------
    # Numeric: samples_per_trace
    # ------------------------------
    for idx, line in enumerate(lines):
        s = match_samples_per_trace(line)
        if s:
            out["samples_per_trace"] = FieldEvidence(
                value=int(s[0]), confidence=0.9, line_refs=[idx + 1]
            )
            break

    # ------------------------------
    # Numeric: record_length_ms
    # ------------------------------
    # Priority 1: explicit pattern "RECORD LENGTH ... MS" or variants
    res = _search_lines(
        lines,
        [
            re.compile(r"RECORD\s+LENGTH\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*MS", re.IGNORECASE),
            re.compile(r"RLEN(?:GTH)?\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*MS", re.IGNORECASE),
        ],
    )
    if res:
        line_no, m = res
        out["record_length_ms"] = FieldEvidence(
            value=float(m.group(1)), confidence=0.9, line_refs=[line_no]
        )

    # Priority 2: derive from samples_per_trace * sample_interval_ms
    if "record_length_ms" not in out:
        si = out.get("sample_interval_ms")
        spt = out.get("samples_per_trace")
        if si and spt:
            out["record_length_ms"] = FieldEvidence(
                value=float(si.value) * int(spt.value),
                confidence=min(si.confidence, spt.confidence),
                line_refs=sorted(set(si.line_refs + spt.line_refs)),
            )

    # ------------------------------
    # Additional high-signal numeric tokens
    # ------------------------------
    res = _search_lines(lines, [re.compile(r"DATA\s+TRACES\s*/\s*RECORD\s+(\d+)", re.IGNORECASE)])
    if res:
        line_no, m = res
        out["data_traces_per_record"] = FieldEvidence(
            value=int(m.group(1)), confidence=0.8, line_refs=[line_no]
        )

    res = _search_lines(lines, [re.compile(r"AUXILIARY\s+TRACES\s*/\s*RECORD\s+(\d+)", re.IGNORECASE)])
    if res:
        line_no, m = res
        out["auxiliary_traces_per_record"] = FieldEvidence(
            value=int(m.group(1)), confidence=0.7, line_refs=[line_no]
        )

    # ------------------------------
    # Free-text tokens
    # ------------------------------
    res = _search_lines(
        lines, [re.compile(r"COMPANY\s*[:=]?\s*([A-Z0-9 &\-_/]+?)(?:\s{2,}|$)", re.IGNORECASE)]
    )
    if res:
        line_no, m = res
        out["company"] = FieldEvidence(
            value=m.group(1).strip().upper(), confidence=0.8, line_refs=[line_no]
        )

    res = _search_lines(
        lines, [re.compile(r"CLIENT\s*[:=]?\s*([A-Z0-9 &\-_/]+?)(?:\s{2,}|$)", re.IGNORECASE)]
    )
    if res:
        line_no, m = res
        out["client"] = FieldEvidence(
            value=m.group(1).strip().upper(), confidence=0.7, line_refs=[line_no]
        )

    res = _search_lines(
        lines, [re.compile(r"AREA\s*[:=]?\s*([A-Z0-9 &\-_/]+?)(?:\s{2,}|$)", re.IGNORECASE)]
    )
    if res:
        line_no, m = res
        out["area"] = FieldEvidence(
            value=m.group(1).strip().upper(), confidence=0.7, line_refs=[line_no]
        )

    # Recording format
    res = _search_lines(
        lines,
        [
            re.compile(
                r"(RECORDING\s+FORMAT|FORMAT\s+THIS\s+REEL)\s*[:=]?\s*([A-Za-z0-9\-_/\. ]+)",
                re.IGNORECASE,
            )
        ],
    )
    if res:
        line_no, m = res
        out["recording_format"] = FieldEvidence(
            value=m.group(2).strip().upper(), confidence=0.75, line_refs=[line_no]
        )

    # Measurement system
    res = _search_lines(lines, [re.compile(r"MEASUREMENT\s+SYSTEM\s*[:=]?\s*([A-Z]+)", re.IGNORECASE)])
    if res:
        line_no, m = res
        ms = m.group(1).strip().upper()
        if ms in ("SI", "METRIC"):
            ms = "METRIC"
        elif ms in ("IMPERIAL", "FEET", "FT"):
            ms = "FEET"
        out["measurement_system"] = FieldEvidence(value=ms, confidence=0.65, line_refs=[line_no])

    return out
