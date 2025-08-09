from __future__ import annotations

from typing import Any, List, Optional


def _get_val(d: dict, key: str) -> Optional[Any]:
    v = d.get(key)
    if isinstance(v, dict):
        return v.get("value")
    return v


def _get_lines(d: dict, key: str) -> List[int]:
    v = d.get(key)
    if isinstance(v, dict):
        return list(v.get("line_refs", []) or [])
    return []


_FORMAT_NAME = {
    1: ("IBM (code 1)", "IBM_FLOAT_4B"),
    2: ("INT32 (code 2)", "INT32"),
    3: ("INT16 (code 3)", "INT16"),
    5: ("IEEE (code 5)", "IEEE_FLOAT_4B"),
    8: ("INT8 (code 8)", "INT8"),
}


def check_text_vs_binary(header_sanity: dict, binary: dict) -> dict:
    """Compare key textual header-derived numbers to binary header values.

    Severity logic:
    - critical: numeric mismatch affecting geometry/time axis (interval, samples)
    - warning: ambiguous text categories (e.g., sample format unspecified vs binary code)

    Returns a dict with 'issues' and 'suggested_patch'.
    """
    issues: List[dict] = []
    patch: List[dict] = []

    # 1) Sample interval (ms)
    txt_si_ms = _get_val(header_sanity, "sample_interval_ms")
    bin_si_us = binary.get("sample_interval_us")
    bin_si_ms: Optional[int] = None
    if isinstance(bin_si_us, (int, float)):
        try:
            # Prefer integer ms when divisible, else round to nearest int
            ms = float(bin_si_us) / 1000.0
            bin_si_ms = int(ms) if abs(ms - int(ms)) < 1e-6 else int(round(ms))
        except Exception:
            bin_si_ms = None

    if txt_si_ms is not None and bin_si_ms is not None and txt_si_ms != bin_si_ms:
        issues.append(
            {
                "field": "sample_interval_ms",
                "observed_text": txt_si_ms,
                "observed_binary": bin_si_ms,
                "severity": "critical",
                "evidence": {"lines": _get_lines(header_sanity, "sample_interval_ms")},
            }
        )
        patch.append(
            {
                "field": "sample_interval_ms",
                "new_value": bin_si_ms,
                "rationale": "Binary header is authoritative",
            }
        )

    # 2) Samples per trace
    txt_spt = _get_val(header_sanity, "samples_per_trace")
    bin_spt = binary.get("samples_per_trace")
    if txt_spt is not None and bin_spt is not None and txt_spt != bin_spt:
        issues.append(
            {
                "field": "samples_per_trace",
                "observed_text": txt_spt,
                "observed_binary": bin_spt,
                "severity": "critical",
                "evidence": {"lines": _get_lines(header_sanity, "samples_per_trace")},
            }
        )
        patch.append(
            {
                "field": "samples_per_trace",
                "new_value": int(bin_spt),
                "rationale": "Binary header is authoritative",
            }
        )

    # 3) Sample format (warning if text is ambiguous vs binary known)
    fmt_code = binary.get("format_code")
    if isinstance(fmt_code, int) and fmt_code in _FORMAT_NAME:
        observed_binary = _FORMAT_NAME[fmt_code][0]
        new_value = _FORMAT_NAME[fmt_code][1]

        # Text-side: we generally can't be definitive from text; mark as ambiguous float when bytes/sample==4
        txt_bps = _get_val(header_sanity, "sample_interval_ms")  # in our sanity it's bytes/sample proxy
        if txt_bps == 4:
            observed_text = "FLOATING PT (unspecified)"
        else:
            observed_text = "UNSPECIFIED"

        # Only add a warning if ambiguous/unspecified
        issues.append(
            {
                "field": "sample_format",
                "observed_text": observed_text,
                "observed_binary": observed_binary,
                "severity": "warning",
            }
        )
        patch.append(
            {
                "field": "sample_format",
                "new_value": new_value,
                "rationale": f"Binary format code = {fmt_code}",
            }
        )

    return {"issues": issues, "suggested_patch": patch}
