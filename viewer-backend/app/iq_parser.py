"""Unified multi-field async intelligent parser for SEG-Y textual headers.

Provides parse_header_iq(lines) coroutine implementing:
 1. One-shot multi-field LLM extraction
 2. Automatic follow-up prompts for low-confidence / missing key fields
 3. Lightweight validation & coercion
 4. Silent empty result if no provider (caller may still choose to proceed)
"""
from __future__ import annotations

import os
import json
import asyncio
import re
from typing import Any, Dict, List, Optional

from app.schemas import HeaderJSON, FieldEvidence

try:  # provider factory (Azure OpenAI, etc.)
    from extract.providers import build_provider_from_env  # type: ignore
except Exception:  # pragma: no cover
    build_provider_from_env = None  # type: ignore


class FieldSpec:
    def __init__(self, name: str, description: str, validator=None):
        self.name = name
        self.description = description.strip()
        self.validator = validator


_GEOMETRY_ALLOWED = {"2D", "3D"}
_ACQ_ALLOWED = {"TOWED STREAMER", "OBN", "OBC", "LAND"}
_SOURCE_ALLOWED = {"AIR GUN", "AIRGUN", "VIBROSEIS", "DYNAMITE"}
_RECEIVER_ALLOWED = {"STREAMER", "GEOPHONE", "HYDROPHONE", "NODE"}


def _validate_year(v: Any, fe: FieldEvidence) -> bool:
    try:
        y = int(v)
        return 1900 <= y <= 2099
    except Exception:
        return False


def _validate_geometry(v: Any, fe: FieldEvidence) -> bool:
    if not isinstance(v, str):
        return False
    up = v.upper()
    return any(g in up.split() for g in _GEOMETRY_ALLOWED) and any(a in up for a in _ACQ_ALLOWED)


def _validate_source(v: Any, fe: FieldEvidence) -> bool:
    if not isinstance(v, str):
        return False
    return any(tok in v.upper().replace("  ", " ") for tok in _SOURCE_ALLOWED)


def _validate_receiver(v: Any, fe: FieldEvidence) -> bool:
    if not isinstance(v, str):
        return False
    return any(tok == v.upper().strip() or tok in v.upper() for tok in _RECEIVER_ALLOWED)


FIELD_SPECS: Dict[str, FieldSpec] = {
    "survey_name": FieldSpec("survey_name", "Survey / project name (as stated)."),
    "area": FieldSpec("area", "Geographic area / basin / line descriptor."),
    "contractor": FieldSpec("contractor", "Acquisition contractor company."),
    "company": FieldSpec("company", "Data owner / client company."),
    "acquisition_year": FieldSpec("acquisition_year", "Acquisition/recorded year YYYY.", validator=_validate_year),
    "sample_interval_ms": FieldSpec("sample_interval_ms", "Sample interval (ms)."),
    "record_length_ms": FieldSpec("record_length_ms", "Record length (ms)."),
    "inline_spacing_m": FieldSpec("inline_spacing_m", "Inline spacing (m)."),
    "crossline_spacing_m": FieldSpec("crossline_spacing_m", "Crossline spacing (m)."),
    "bin_size_m": FieldSpec("bin_size_m", "Nominal bin size (m)."),
    "geometry": FieldSpec("geometry", "2D/3D + environment (TOWED STREAMER | OBN | OBC | LAND).", validator=_validate_geometry),
    "source_type": FieldSpec("source_type", "Source type (AIR GUN/AIRGUN, VIBROSEIS, DYNAMITE).", validator=_validate_source),
    "receiver_type": FieldSpec("receiver_type", "Receiver type (STREAMER, GEOPHONE, HYDROPHONE, NODE).", validator=_validate_receiver),
    "datum": FieldSpec("datum", "Coordinate / vertical datum."),
    "srd_m": FieldSpec("srd_m", "Seismic reference datum elevation (m)."),
    "crs_hint": FieldSpec("crs_hint", "CRS hint (e.g., EPSG:XXXX, UTM zone)."),
    "vessel": FieldSpec("vessel", "Survey vessel name (MV / R/V ...)."),
}


def _format_lines(lines: List[str]) -> str:
    """Format up to 40 header lines with CXX prefixes for prompting."""
    out = []
    for i, line in enumerate(lines[:40]):
        # Ensure max 80 chars like SEG-Y textual standard; strip trailing newline
        out.append(f"C{str(i+1).zfill(2)} {line[:80].rstrip()}")
    return "\n".join(out)


def _build_multi_field_prompt(lines: List[str]) -> str:
    spec_lines = [f"- {fs.name}: {fs.description}" for fs in FIELD_SPECS.values()]
    spec_block = "\n".join(spec_lines)
    return (
        "You are an expert geophysical metadata parser. Extract as many fields as confidently present from a SEG-Y textual header.\n"
        "Return STRICT JSON ONLY: top-level object where each key is a field name and value is { 'value': <primitive>, 'confidence': <0..1>, 'line_refs': [<1-based ints>] }.\n"
        "Omit unknown fields (do NOT include them with empty values). Never invent.\n"
        "Field descriptions:\n" + spec_block + "\n"
        "Rules: Provide at least one line_ref per field (1..40). Year must be 1900-2099. Units: ms or m where applicable. No markdown fences.\n"
        "Header lines (C01..C40):\n" + _format_lines(lines) + "\n"
    )


def _get_provider():
    if build_provider_from_env:
        try:
            return build_provider_from_env()
        except Exception:  # pragma: no cover
            return None
    return None


def _coerce_field(name: str, raw: Dict[str, Any]) -> Optional[FieldEvidence]:
    if not isinstance(raw, dict):
        return None
    val = raw.get("value")
    conf = raw.get("confidence", 0.0)
    try:
        conf = float(conf)
    except Exception:
        conf = 0.0
    line_refs = raw.get("line_refs", []) or []
    if not isinstance(line_refs, list):
        line_refs = []
    clean_refs: List[int] = []
    for r in line_refs:
        try:
            ri = int(r)
            if 1 <= ri <= 40:
                clean_refs.append(ri)
        except Exception:
            continue
    if not clean_refs:
        return None
    fe = FieldEvidence(value=val, confidence=max(0.0, min(1.0, conf)), line_refs=clean_refs)
    spec = FIELD_SPECS.get(name)
    if spec and spec.validator and not spec.validator(val, fe):
        return None
    if name in {"sample_interval_ms", "record_length_ms", "inline_spacing_m", "crossline_spacing_m", "bin_size_m", "srd_m"}:
        try:
            fe.value = float(val)
        except Exception:
            return None
    if name == "acquisition_year":
        try:
            fe.value = int(val)
        except Exception:
            return None
    return fe


def _parse_multi_field_json(text: Any) -> Dict[str, FieldEvidence]:
    if isinstance(text, dict):
        raw = text
    else:
        try:
            raw = json.loads(str(text))
        except Exception:
            s = str(text)
            start, end = s.find("{"), s.rfind("}")
            if start != -1 and end > start:
                try:
                    raw = json.loads(s[start : end + 1])
                except Exception:
                    return {}
            else:
                return {}
    if not isinstance(raw, dict):
        return {}
    if "header" in raw and isinstance(raw["header"], dict):  # unwrap legacy nested object
        raw = raw["header"]
    out: Dict[str, FieldEvidence] = {}
    for k, v in raw.items():
        if k in HeaderJSON.model_fields:
            fe = _coerce_field(k, v)
            if fe:
                out[k] = fe
    return out


async def _provider_infer(provider, prompt: str) -> Any:
    import anyio  # local import to avoid mandatory dependency at test import time

    def _call():
        return provider.infer(prompt)

    return await anyio.to_thread.run_sync(_call)


async def _followup(provider, lines: List[str], field: str) -> Optional[FieldEvidence]:
    spec = FIELD_SPECS.get(field)
    if not spec:
        return None
    prompt = (
        f"Extract ONLY field '{field}'. Description: {spec.description}.\n"
        "Return STRICT JSON {\"value\": <primitive or empty>, \"confidence\": <0..1>, \"line_refs\": [<ints>] }.\n"
        "If unknown return {\"value\": \"\", \"confidence\": 0, \"line_refs\": []}.\n"
        "Header lines:\n" + _format_lines(lines)
    )
    try:
        raw = await _provider_infer(provider, prompt)
    except Exception:
        return None
    parsed = _parse_multi_field_json({field: raw if isinstance(raw, dict) else raw})
    return parsed.get(field)


async def parse_header_iq(lines: List[str]) -> HeaderJSON:
    """Parse textual header lines (<=40) returning structured HeaderJSON.

    Strategy:
    1. One-shot multi-field extraction.
    2. Targeted follow-up prompts for (a) missing important fields or (b) low-confidence fields.
    3. Basic field-level validation / coercion.
    4. Silent fallback to empty HeaderJSON if provider unavailable.
    """
    provider = _get_provider()
    if not provider:
        # Heuristic regex fallback (legacy minimal extraction without LLM)
        return HeaderJSON(**_regex_fallback(lines))

    prompt = _build_multi_field_prompt(lines)
    try:
        raw = await _provider_infer(provider, prompt)
    except Exception:
        return HeaderJSON()

    fields = _parse_multi_field_json(raw)

    if not fields:  # if model returned nothing, attempt regex fallback to supply basics
        fields = _regex_fallback(lines)

    important = ("survey_name", "contractor", "acquisition_year")
    low_conf = [k for k, fe in fields.items() if fe.confidence < 0.5]
    missing = [k for k in important if k not in fields]
    targets = sorted(set(low_conf + missing))

    if targets:
        async def _task(fname: str):
            try:
                fe = await _followup(provider, lines, fname)
                return fname, fe
            except Exception:  # pragma: no cover
                return fname, None

        results = await asyncio.gather(*[_task(f) for f in targets])
        for fname, fe in results:
            if fe and (fname not in fields or fe.confidence > fields[fname].confidence):
                fields[fname] = fe

    payload = {k: v for k, v in fields.items() if k in HeaderJSON.model_fields}

    if os.getenv("IQ_PARSER_V2_DEBUG") == "1":  # optional debug trace
        try:  # pragma: no cover
            import logging
            logging.info("parse_header_iq fields=%s", {k: v.confidence for k, v in payload.items()})
        except Exception:
            pass

    return HeaderJSON(**payload)


__all__ = ["parse_header_iq"]


# -----------------------
# Regex fallback heuristics
# -----------------------

def _regex_fallback(lines: List[str]) -> Dict[str, FieldEvidence]:
    out: Dict[str, FieldEvidence] = {}

    def _match(patterns, subset=None):
        search_space = lines if subset is None else lines[:subset]
        for idx, line in enumerate(search_space):
            u = line.upper()
            for pat in patterns:
                m = pat.search(u)
                if m:
                    return idx + 1, m
        return None

    # Survey name
    res = _match([re.compile(r"SURVEY\s*[:=]?\s*([A-Z0-9_\- ]{3,})")], subset=5)
    if res:
        ln, m = res
        val = m.group(1).strip().rstrip(" .")
        out["survey_name"] = FieldEvidence(value=val, confidence=0.9, line_refs=[ln])

    # Area
    res = _match([re.compile(r"AREA\s*[:=]?\s*([A-Z0-9_\- ]{3,})")], subset=6)
    if res:
        ln, m = res
        val = m.group(1).strip().rstrip(" .")
        out["area"] = FieldEvidence(value=val.title(), confidence=0.85, line_refs=[ln])

    # Contractor
    res = _match([re.compile(r"CONTRACTOR\s*[:=]?\s*([A-Z0-9 &_\-]{3,})")])
    if res:
        ln, m = res
        raw = m.group(1).strip().rstrip(" .")
        out["contractor"] = FieldEvidence(value=raw.title(), confidence=0.85, line_refs=[ln])

    # Acquisition year
    res = _match([
        re.compile(r"ACQUISITION\s+YEAR\D+(19|20)(\d{2})"),
        re.compile(r"RECORDED\s+YEAR\D+(19|20)(\d{2})"),
    ])
    if res:
        ln, m = res
        year = int(m.group(1) + m.group(2))
        out["acquisition_year"] = FieldEvidence(value=year, confidence=0.9, line_refs=[ln])

    # Sample interval & record length (can be on same line)
    for idx, line in enumerate(lines):
        u = line.upper()
        m1 = re.search(r"SAMPLE\s+INTERVAL\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*MS", u) or re.search(r"DT\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*MS", u)
        if m1 and "sample_interval_ms" not in out:
            try:
                out["sample_interval_ms"] = FieldEvidence(value=float(m1.group(1)), confidence=0.9, line_refs=[idx + 1])
            except Exception:
                pass
        m2 = re.search(r"RECORD\s+LENGTH\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*MS", u) or re.search(r"RLEN(GTH)?\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*MS", u)
        if m2 and "record_length_ms" not in out:
            try:
                val = float(m2.group(1) if m2.group(1) else m2.group(2))
            except Exception:
                try:
                    val = float(m2.group(2))
                except Exception:
                    val = None
            if val is not None:
                out["record_length_ms"] = FieldEvidence(value=val, confidence=0.9, line_refs=[idx + 1])
    # Inline/Crossline/bin size
    for idx, line in enumerate(lines):
        u = line.upper()
        if "inline_spacing_m" not in out:
            m = re.search(r"INLINE\s+SPACING\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*M", u)
            if m:
                try:
                    out["inline_spacing_m"] = FieldEvidence(value=float(m.group(1)), confidence=0.8, line_refs=[idx + 1])
                except Exception:
                    pass
        if "crossline_spacing_m" not in out:
            m = re.search(r"CROSSLINE\s+SPACING\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*M", u)
            if m:
                try:
                    out["crossline_spacing_m"] = FieldEvidence(value=float(m.group(1)), confidence=0.8, line_refs=[idx + 1])
                except Exception:
                    pass
        if "bin_size_m" not in out:
            m = re.search(r"BIN\s+SIZE\s*[:=]?\s*([0-9]*\.?[0-9]+)\s*M", u)
            if m:
                try:
                    out["bin_size_m"] = FieldEvidence(value=float(m.group(1)), confidence=0.75, line_refs=[idx + 1])
                except Exception:
                    pass
    return out
