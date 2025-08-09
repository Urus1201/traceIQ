from __future__ import annotations

from typing import Dict, List, Tuple

from extract.value_extractors import (
    match_samples_per_trace,
    match_bytes_per_sample,
)


def derive_record_length_ms(sample_interval_ms: int, samples_per_trace: int) -> int:
    """Compute record length (ms) as sample_interval_ms * samples_per_trace.

    This is a simple micro-QC sanity derivation; callers ensure inputs are ints.
    """
    return int(sample_interval_ms) * int(samples_per_trace)


def sanity_derive_from_text(lines: List[str]) -> Dict[str, dict]:
    """Derive simple sanity metrics from textual header lines.

    For L6, compute record_length_ms using the numeric fields present:
    - samples_per_trace from "SAMPLES/TRACE"
    - bytes_per_sample (repurposed here as a proxy for sample interval in ms) from "BYTES/SAMPLE"

    Returns a dict with key 'record_length_ms' containing FieldEvidence-like dict:
    {
        'value': 3000,
        'confidence': 0.8,
        'line_refs': [6],
        'raw_spans': [(start_4, end_4), (start_750, end_750)]
    }
    If values are not available, returns an empty dict.
    """
    result: Dict[str, dict] = {}

    if len(lines) >= 6:
        l6 = lines[5]
        s = match_samples_per_trace(l6)
        b = match_bytes_per_sample(l6)
        if s and b:
            samples = s[0]
            sample_interval_ms = b[0]
            value = derive_record_length_ms(sample_interval_ms, samples)
            spans: List[Tuple[int, int]] = []
            spans.append(b[1])
            spans.append(s[1])
            result["record_length_ms"] = {
                "value": value,
                "confidence": 0.8,
                "line_refs": [6],
                "raw_spans": spans,
            }

    return result
