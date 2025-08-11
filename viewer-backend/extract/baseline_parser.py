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


def _to_float(num: str) -> float:
    """Parse a numeric string allowing separators like commas/underscores/spaces.

    Examples: "2,000" -> 2000.0, "1 000.5" -> 1000.5
    """
    cleaned = num.replace(",", "").replace("_", "").replace(" ", "")
    # normalize micro symbol if present elsewhere in the string (handled in regex units anyway)
    return float(cleaned)


def _clean_text_capture(raw: str, label_hint: Optional[str] = None) -> Optional[str]:
    """Normalize free-text captures; drop empty or placeholder values.

    Returns an UPPERCASED string or None if deemed empty/placeholder.
    """
    val = (raw or "").strip()
    if not val:
        return None
    upper = val.upper()
    placeholders = {"N/A", "NA", "NONE", "UNKNOWN", "NULL", "-"}
    if label_hint:
        placeholders.add(label_hint.upper())
    if upper in placeholders:
        return None
    return upper


def parse_baseline(lines: List[str]) -> Dict[str, FieldEvidence]:
    """Deterministic baseline extraction from textual header.

    Returns a dict[field_name] -> FieldEvidence. Always includes line_refs when present.
    """
    out: Dict[str, FieldEvidence] = {}

    # ------------------------------
    # Numeric: sample_interval_ms
    # ------------------------------
    # Priority 1: explicit with units (MS/MSEC/USEC/US/SEC)
    for idx, line in enumerate(lines):
        # First try library extractor (commonly handles ms)
        v = match_sample_interval_ms(line)
        if v:
            out["sample_interval_ms"] = FieldEvidence(
                value=float(v[0]), confidence=0.9, line_refs=[idx + 1]
            )
            break
        # Fallback explicit microseconds / seconds
        m = re.search(
            r"SAMPLE\s+INTER\w*\s*[:=]?\s*([0-9,._ ]*\.?[0-9]+)\s*(MSEC|MILLISECONDS?|MS|USEC|MICROSECONDS?|US|µS|S|SEC|SECONDS?)\b",
            line,
            re.IGNORECASE,
        )
        if m:
            raw = _to_float(m.group(1))
            unit = m.group(2).upper()
            if unit in ("MS", "MSEC", "MILLISECONDS", "MILLISECOND"):
                val_ms = raw
            elif unit in ("USEC", "US", "MICROSECONDS", "MICROSECOND", "µS"):
                val_ms = raw / 1000.0
            elif unit in ("S", "SEC", "SECONDS", "SECOND"):
                val_ms = raw * 1000.0
            else:
                val_ms = raw
            out["sample_interval_ms"] = FieldEvidence(
                value=float(val_ms), confidence=0.88, line_refs=[idx + 1]
            )
            break

    # Priority 2: unitless number with heuristic normalization (common: microseconds 2000/4000)
    if "sample_interval_ms" not in out:
        for idx, line in enumerate(lines):
            m = re.search(
                r"SAMPLE\s+INTER\w*\s*[:=]?\s*([0-9,._ ]*\.?[0-9]+)(?:\s|$)",
                line,
                re.IGNORECASE,
            )
            if m:
                raw = _to_float(m.group(1))
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
    # Priority 1: explicit pattern with units (MS/MSEC/SEC)
    res = _search_lines(
        lines,
        [
            re.compile(
                r"RECORD\s+LENGTH\s*[:=]?\s*([0-9,._ ]*\.?[0-9]+)\s*(MSEC|MILLISECONDS?|MS|S|SEC|SECONDS?)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"RLEN(?:GTH)?\s*[:=]?\s*([0-9,._ ]*\.?[0-9]+)\s*(MSEC|MILLISECONDS?|MS|S|SEC|SECONDS?)\b",
                re.IGNORECASE,
            ),
        ],
    )
    if res:
        line_no, m = res
        raw = _to_float(m.group(1))
        unit = m.group(2).upper()
        if unit in ("MS", "MSEC", "MILLISECONDS", "MILLISECOND"):
            rl_ms = raw
        elif unit in ("S", "SEC", "SECONDS", "SECOND"):
            rl_ms = raw * 1000.0
        else:
            rl_ms = raw
        out["record_length_ms"] = FieldEvidence(
            value=float(rl_ms), confidence=0.9, line_refs=[line_no]
        )

    # Priority 2: derive from samples_per_trace * sample_interval_ms
    if "record_length_ms" not in out:
        si = out.get("sample_interval_ms")
        spt = out.get("samples_per_trace")
        if si and spt:
            derived = float(si.value) * int(spt.value)
            # reduce tiny float artifacts
            if abs(derived - round(derived)) < 1e-6:
                derived = float(round(derived))
            out["record_length_ms"] = FieldEvidence(
                value=derived,
                confidence=min(si.confidence, spt.confidence),
                line_refs=sorted(set(si.line_refs + spt.line_refs)),
            )

    # ------------------------------
    # Additional high-signal numeric tokens
    # ------------------------------
    # Broaden to match TRACE/RECORD, TRACES/RECORDS, with optional DATA prefix
    res = _search_lines(
        lines,
        [
            re.compile(r"(?:DATA\s+)?TRACES?\s*/\s*RECORDS?\s*[:=]?\s*(?P<n>\d+)", re.IGNORECASE),
        ],
    )
    if res:
        line_no, m = res
        n = int(m.group("n")) if "n" in m.groupdict() else int(m.group(1))
        out["data_traces_per_record"] = FieldEvidence(
            value=n, confidence=0.8, line_refs=[line_no]
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
        lines, [re.compile(r"COMPANY\s*[:=]?\s*([A-Z0-9 .,&'\-_/]+?)(?:\s{2,}|$)", re.IGNORECASE)]
    )
    if res:
        line_no, m = res
        val = _clean_text_capture(m.group(1), label_hint="COMPANY")
        if val:
            out["company"] = FieldEvidence(
                value=val, confidence=0.8, line_refs=[line_no]
            )

    res = _search_lines(
        lines, [re.compile(r"CLIENT\s*[:=]?\s*([A-Z0-9 .,&'\-_/]+?)(?:\s{2,}|$)", re.IGNORECASE)]
    )
    if res:
        line_no, m = res
        val = _clean_text_capture(m.group(1), label_hint="CLIENT")
        if val:
            out["client"] = FieldEvidence(
                value=val, confidence=0.7, line_refs=[line_no]
            )

    res = _search_lines(
        lines, [re.compile(r"AREA\s*[:=]?\s*([A-Z0-9 .,&'\-_/]+?)(?:\s{2,}|$)", re.IGNORECASE)]
    )
    if res:
        line_no, m = res
        val = _clean_text_capture(m.group(1), label_hint="AREA")
        if val:
            out["area"] = FieldEvidence(
                value=val, confidence=0.7, line_refs=[line_no]
            )

    # Contractor
    res = _search_lines(
        lines, [re.compile(r"CONTRACTOR\s*[:=]?\s*([A-Z0-9 .,&'\-_/]+?)(?:\s{2,}|$)", re.IGNORECASE)]
    )
    if res:
        line_no, m = res
        val = _clean_text_capture(m.group(1), label_hint="CONTRACTOR")
        if val:
            out["contractor"] = FieldEvidence(
                value=val, confidence=0.7, line_refs=[line_no]
            )

    # Survey/Project name
    res = _search_lines(
        lines, [re.compile(r"PROJECT\s+NAME\s*[:=]?\s*(.+?)(?:\s{2,}|$)", re.IGNORECASE)]
    )
    if res:
        line_no, m = res
        val = _clean_text_capture(m.group(1), label_hint="PROJECT NAME")
        if val:
            out["survey_name"] = FieldEvidence(
                value=val, confidence=0.75, line_refs=[line_no]
            )

    # Acquisition year (from DATE: YYYY ...)
    res = _search_lines(
        lines, [re.compile(r"\bDATE\s*[:=]?\s*(\d{4})\b", re.IGNORECASE)]
    )
    if res:
        line_no, m = res
        try:
            year = int(m.group(1))
            if 1900 <= year <= 2100:
                out["acquisition_year"] = FieldEvidence(
                    value=year, confidence=0.6, line_refs=[line_no]
                )
        except Exception:
            pass

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

    # Endianness hint — flag little-endian as non-standard per SEG-Y Rev1
    res = _search_lines(
        lines, [re.compile(r"\b(LITTLE|BIG)\s+ENDIAN\b", re.IGNORECASE)]
    )
    if res:
        line_no, m = res
        if m.group(1).strip().upper() == "LITTLE":
            note = (
                "Textual header indicates LITTLE ENDIAN; SEG-Y Rev1 specifies big-endian. File may be non-standard."
            )
            out.setdefault("notes", FieldEvidence(value=note, confidence=0.5, line_refs=[line_no]))

    return out
