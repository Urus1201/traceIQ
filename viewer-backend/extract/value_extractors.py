from __future__ import annotations

import re
from typing import Optional, Tuple


_MS_RE = re.compile(r"(\d+)\s*MS", re.IGNORECASE)


def extract_ms(text: str) -> Optional[int]:
    m = _MS_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def extract_int_after(label_regex: str, text: str) -> Optional[Tuple[int, Tuple[int, int]]]:
    pat = re.compile(rf"{label_regex}\s*[:=]?\s*(\d+)", re.IGNORECASE)
    m = pat.search(text)
    if not m:
        return None
    val = int(m.group(1))
    return val, m.span(1)


# Specific robust matchers

def match_data_traces_per_record(text: str) -> Optional[Tuple[int, Tuple[int, int]]]:
    return extract_int_after(r"DATA\s+TRACES/RECORD", text)


def match_aux_traces_per_record(text: str) -> Optional[Tuple[int, Tuple[int, int]]]:
    return extract_int_after(r"AUXILIARY\s+TRACES/RECORD", text)


def match_samples_per_trace(text: str) -> Optional[Tuple[int, Tuple[int, int]]]:
    return extract_int_after(r"SAMPLES/TRACE", text)


def match_bytes_per_sample(text: str) -> Optional[Tuple[int, Tuple[int, int]]]:
    return extract_int_after(r"BYTES/SAMPLE", text)


def match_format_this_reel(text: str) -> Optional[Tuple[str, Tuple[int, int]]]:
    pat = re.compile(r"FORMAT\s+THIS\s+REEL\s*[:=]?\s*(SEGY|SEG-Y)", re.IGNORECASE)
    m = pat.search(text)
    if not m:
        return None
    return m.group(1).upper().replace("-", ""), m.span(1)


def match_sample_interval_ms(text: str) -> Optional[Tuple[int, Tuple[int, int]]]:
    # Accept INTERVAL, INTERNAL, INTERxxx
    pat = re.compile(r"SAMPLE\s+INTER\w*\s*[:=]?\s*(\d+)\s*MS", re.IGNORECASE)
    m = pat.search(text)
    if not m:
        return None
    return int(m.group(1)), m.span(1)
