from __future__ import annotations

import re
from typing import List, Optional, Tuple

from app.schemas import HeaderJSON, FieldEvidence


def _match_on_lines(lines: List[str], patterns: List[re.Pattern[str]]) -> Optional[Tuple[int, re.Match]]:
    for idx, line in enumerate(lines):
        upper = line.upper()
        for pat in patterns:
            m = pat.search(upper)
            if m:
                return (idx + 1, m)
    return None


def _extract_float_group(m: re.Match, group: int = 1) -> Optional[float]:
    try:
        return float(m.group(group))
    except Exception:
        return None


def _extract_int_group(m: re.Match, group: int = 1) -> Optional[int]:
    try:
        return int(m.group(group))
    except Exception:
        return None


def parse_header_iq(lines: List[str]) -> HeaderJSON:
    """Heuristically extract key fields from SEG-Y textual header lines (40x80).
    Returns a HeaderJSON with FieldEvidence for detected values.
    """
    hj = HeaderJSON()

    # Survey name: look in first few lines for SURVEY or PROJECT
    res = _match_on_lines(
        lines[:5],
        [re.compile(r"SURVEY\s*[:=]?\s*([A-Z0-9_\- ]{3,})"), re.compile(r"PROJECT\s*[:=]?\s*([A-Z0-9_\- ]{3,})")],
    )
    if res:
        line_no, m = res
        val = m.group(1).strip().rstrip(" .")
        hj.survey_name = FieldEvidence(value=val, confidence=0.9, line_refs=[line_no])

    # Area: look for AREA
    res = _match_on_lines(lines[:6], [re.compile(r"AREA\s*[:=]?\s*([A-Z0-9_\- ]{3,})")])
    if res:
        line_no, m = res
        val = m.group(1).strip().rstrip(" .")
        hj.area = FieldEvidence(value=val.title(), confidence=0.85, line_refs=[line_no])

    # Contractor: CONTRACTOR or COMPANY
    res = _match_on_lines(
        lines,
        [re.compile(r"CONTRACTOR\s*[:=]?\s*([A-Z0-9 &_\-]{3,})"), re.compile(r"COMPANY\s*[:=]?\s*([A-Z0-9 &_\-]{3,})")],
    )
    if res:
        line_no, m = res
        val = m.group(1).strip().rstrip(" .")
        hj.contractor = FieldEvidence(value=val.title(), confidence=0.85, line_refs=[line_no])

    # Acquisition year: ACQUISITION YEAR or RECORDED YEAR or YEAR 20xx/19xx
    res = _match_on_lines(
        lines,
        [
            re.compile(r"ACQUISITION\s+YEAR\D+(19|20)(\d{2})"),
            re.compile(r"RECORDED\s+YEAR\D+(19|20)(\d{2})"),
            re.compile(r"\b(19|20)(\d{2})\b.*ACQUISIT"),
        ],
    )
    if res:
        line_no, m = res
        year = int(m.group(1) + m.group(2))
        hj.acquisition_year = FieldEvidence(value=year, confidence=0.9, line_refs=[line_no])

    # Sample interval (ms)
    res = _match_on_lines(
        lines,
        [re.compile(r"SAMPLE\s+INTERVAL\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*MS"), re.compile(r"DT\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*MS")],
    )
    if res:
        line_no, m = res
        val = _extract_float_group(m)
        hj.sample_interval_ms = FieldEvidence(value=val, confidence=0.9, line_refs=[line_no])

    # Record length (ms)
    res = _match_on_lines(
        lines,
        [re.compile(r"RECORD\s+LENGTH\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*MS"), re.compile(r"RLEN(GTH)?\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*MS")],
    )
    if res:
        line_no, m = res
        val = _extract_float_group(m)
        hj.record_length_ms = FieldEvidence(value=val, confidence=0.9, line_refs=[line_no])

    # Inline/Crossline spacing and Bin size (m)
    res = _match_on_lines(
        lines,
        [
            re.compile(r"INLINE\s+SPACING\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*M"),
        ],
    )
    if res:
        line_no, m = res
        val = _extract_float_group(m)
        hj.inline_spacing_m = FieldEvidence(value=val, confidence=0.8, line_refs=[line_no])

    res = _match_on_lines(
        lines,
        [
            re.compile(r"CROSSLINE\s+SPACING\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*M"),
        ],
    )
    if res:
        line_no, m = res
        val = _extract_float_group(m)
        hj.crossline_spacing_m = FieldEvidence(value=val, confidence=0.8, line_refs=[line_no])

    res = _match_on_lines(
        lines,
        [
            re.compile(r"BIN\s+SIZE\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*M"),
        ],
    )
    if res:
        line_no, m = res
        val = _extract_float_group(m)
        hj.bin_size_m = FieldEvidence(value=val, confidence=0.75, line_refs=[line_no])

    # Geometry
    res = _match_on_lines(lines, [re.compile(r"\b(2D|3D)\b.*(TOWED\s+STREAMER|OBN|OBC|LAND)")])
    if res:
        line_no, m = res
        hj.geometry = FieldEvidence(value=m.group(0).title(), confidence=0.8, line_refs=[line_no])

    # Source type
    res = _match_on_lines(lines, [re.compile(r"AIR\s*GUN|AIRGUN|VIBROSEIS|DYNAMITE")])
    if res:
        line_no, m = res
        hj.source_type = FieldEvidence(value=m.group(0).title().replace(" ", ""), confidence=0.8, line_refs=[line_no])

    # Receiver type
    res = _match_on_lines(lines, [re.compile(r"STREAMER|GEOPHONE|HYDROPHONE|NODE")])
    if res:
        line_no, m = res
        hj.receiver_type = FieldEvidence(value=m.group(0).title(), confidence=0.8, line_refs=[line_no])

    # Datum and SRD
    res = _match_on_lines(lines, [re.compile(r"DATUM\s*[:=]?\s*([A-Z0-9 \-]+)")])
    if res:
        line_no, m = res
        hj.datum = FieldEvidence(value=m.group(1).strip(), confidence=0.6, line_refs=[line_no])

    res = _match_on_lines(lines, [re.compile(r"\bSRD\b\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*M")])
    if res:
        line_no, m = res
        hj.srd_m = FieldEvidence(value=_extract_float_group(m), confidence=0.7, line_refs=[line_no])

    # CRS hint
    res = _match_on_lines(lines, [re.compile(r"EPSG\s*[:=]?\s*(\d{4,5})|UTM\s+\d{1,2}[NS]")])
    if res:
        line_no, m = res
        hj.crs_hint = FieldEvidence(value=m.group(0).strip(), confidence=0.7, line_refs=[line_no])

    # Vessel
    res = _match_on_lines(lines, [re.compile(r"\b(MV|RV|R/V)\s+[A-Z0-9 \-]{3,}")])
    if res:
        line_no, m = res
        hj.vessel = FieldEvidence(value=m.group(0).title(), confidence=0.7, line_refs=[line_no])

    # Notes: If we matched nothing else, or to aggregate
    # For now leave None; could combine unmatched C-lines later.

    return hj
