from __future__ import annotations

import re
from typing import List, Optional, Tuple

import json
from collections import Counter

try:
    # Best-effort LLM hooks; keep optional to avoid breaking tests when no provider is set
    from extract.llm_fallback import run_llm  # type: ignore
except Exception:  # pragma: no cover
    run_llm = None  # type: ignore

try:
    from extract.providers import build_provider_from_env  # type: ignore
except Exception:  # pragma: no cover
    build_provider_from_env = None  # type: ignore

from app.schemas import HeaderJSON, FieldEvidence

# Minimal guardrails — used only as a last resort when LLM is unavailable or unsure
LEGAL_RX = re.compile(r"\b(liable|warranty|merchantability|damages?|loss(?:es)?|consequential|fitness)\b", re.I)
ORG_RX   = re.compile(r"\b(inc|ltd|llc|geophysical|services|petroleum|energy|exploration|company|s\.a\.|s\.p\.a\.|gmbh|bv|pty|plc)\b", re.I)

def _validate_contractor_candidate(value: str, line_no: int) -> bool:
    v = (value or "").strip()
    if not v:
        return False
    if LEGAL_RX.search(v):
        return False
    # Prefer org-shaped tokens or tail lines (C35–C40)
    tail_ok = line_no >= 35
    return bool(ORG_RX.search(v) or tail_ok)

def _get_llm_provider():
    try:
        if build_provider_from_env:
            return build_provider_from_env()
    except Exception:
        return None
    return None

def _parse_json_safely(text: str):
    # Accept "```json ... ```" or plain JSON; fall back to best-effort slice
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        import re as _re
        m = _re.search(r"\{[\s\S]*\}\s*$", text)
        if m:
            return json.loads(m.group(0))
    except Exception:
        return None

def _llm_tag_lines(lines: List[str]):
    """Ask the LLM to tag each line with a coarse label so we can avoid LEGAL lines.
    Returns a dict {1-based line_no: label} or None on failure.
    """
    if not run_llm:
        return None
    provider = _get_llm_provider()
    if not provider:
        return None
    prompt = (
        "You are given 40 lines of a SEG-Y textual (EBCDIC) header. "
        "Tag each line with one label from: "
        "LEGAL_DISCLAIMER, ORG_CONTRACTOR, ORG_COMPANY, CONTACT_INFO, ACQ_PARAM, FORMAT_QC, OTHER.\n"
        "Return strict JSON as an array of objects: [{\"line\": <int 1-40>, \"label\": <string>, \"reason\": <short>}]. "
        "Do not include explanations outside JSON."
    )
    joined = "\n".join(f"C{str(i+1).zfill(2)} {ln}" for i, ln in enumerate(lines[:40]))
    resp = run_llm(prompt + "\n\nHEADER:\n" + joined, provider=provider)
    data = _parse_json_safely(resp if isinstance(resp, str) else str(resp))
    if not isinstance(data, list):
        return None
    out = {}
    for item in data:
        try:
            ln = int(item.get("line"))
            lab = str(item.get("label") or "").strip().upper() or "OTHER"
            if 1 <= ln <= min(40, len(lines)):
                out[ln] = lab
        except Exception:
            continue
    return out if out else None


def _llm_extract_contractor(lines: List[str], k_votes: int = 3):
    """Use the LLM to extract contractor with constraints + self-consistency voting.
    Returns FieldEvidence or None.
    """
    if not run_llm:
        return None
    provider = _get_llm_provider()
    if not provider:
        return None

    tags = _llm_tag_lines(lines) or {}

    def one_shot():
        allowed_lines = [i for i in range(1, min(40, len(lines)) + 1)
                         if tags.get(i, "OTHER") not in ("LEGAL_DISCLAIMER",)]
        snippet = "\n".join(f"C{str(i).zfill(2)} {lines[i-1]}" for i in allowed_lines)
        prompt = (
            "From the allowed lines of a SEG-Y textual header below, extract the CONTRACTOR company name.\n"
            "Rules: It must be an organization (not legal text). Prefer tail lines (C35–C40). "
            "Prefer names that co-occur with URL/email/phone. If unsure, return empty.\n"
            "Return strict JSON: {\"value\": <string or \"\" if unknown>, \"line_refs\": [<ints>], \"confidence\": <0..1>}. "
            "No extra text.\n"
        )
        resp = run_llm(prompt + "\n\nALLOWED LINES:\n" + snippet, provider=provider)
        data = _parse_json_safely(resp if isinstance(resp, str) else str(resp))
        if not isinstance(data, dict):
            return None
        val = (data.get("value") or "").strip()
        lrefs = [int(x) for x in (data.get("line_refs") or []) if isinstance(x, (int, float, str))]
        conf = float(data.get("confidence") or 0.0)
        if not val:
            return None
        # minimal guardrail check
        if not lrefs:
            return None
        # Keep only first ref in range and ensure it passes simple validation
        ln = int(lrefs[0])
        if not _validate_contractor_candidate(val, ln):
            return None
        return val, ln, conf

    votes: List[Tuple[str, int, float]] = []
    for _ in range(max(1, k_votes)):
        try:
            r = one_shot()
            if r:
                votes.append(r)
        except Exception:
            continue

    if not votes:
        return None

    # Majority vote by (value, line)
    key_counts = Counter((v[0], v[1]) for v in votes)
    (best_val, best_ln), _ = key_counts.most_common(1)[0]
    best_conf = max((v[2] for v in votes if v[0] == best_val and v[1] == best_ln), default=0.7)

    return FieldEvidence(value=best_val, confidence=min(1.0, best_conf), line_refs=[best_ln])


# --- Additional LLM-first extractors (company, datum, vessel) ---

def _llm_extract_company(lines: List[str], k_votes: int = 3):
    """LLM-driven company name extraction (distinct from contractor if present).
    Returns FieldEvidence or None. Fallback is handled by caller.
    """
    if not run_llm:
        return None
    provider = _get_llm_provider()
    if not provider:
        return None

    tags = _llm_tag_lines(lines) or {}

    def one_shot():
        # Allow all non-legal lines; company may appear in header or footer
        allowed_lines = [i for i in range(1, min(40, len(lines)) + 1)
                         if tags.get(i, "OTHER") not in ("LEGAL_DISCLAIMER",)]
        snippet = "\n".join(f"C{str(i).zfill(2)} {lines[i-1]}" for i in allowed_lines)
        prompt = (
            "From the allowed lines of a SEG-Y textual header, extract the COMPANY (data owner/client) name.\n"
            "Rules: It must be an organization (not legal text). Prefer lines that look like orgs (Ltd, Inc, LLC, Geophysical, Services, Petroleum, Energy, Exploration, Company).\n"
            "Return strict JSON: {\"value\": <string or \"\" if unknown>, \"line_refs\": [<ints>], \"confidence\": <0..1>}\n"
        )
        resp = run_llm(prompt + "\n\nALLOWED LINES:\n" + snippet, provider=provider)
        data = _parse_json_safely(resp if isinstance(resp, str) else str(resp))
        if not isinstance(data, dict):
            return None
        val = (data.get("value") or "").strip()
        lrefs = [int(x) for x in (data.get("line_refs") or []) if isinstance(x, (int, float, str))]
        conf = float(data.get("confidence") or 0.0)
        if not val or not lrefs:
            return None
        ln = int(lrefs[0])
        if not _validate_contractor_candidate(val, ln):
            return None
        return val, ln, conf

    votes: List[Tuple[str, int, float]] = []
    for _ in range(max(1, k_votes)):
        try:
            r = one_shot()
            if r:
                votes.append(r)
        except Exception:
            continue

    if not votes:
        return None
    key_counts = Counter((v[0], v[1]) for v in votes)
    (best_val, best_ln), _ = key_counts.most_common(1)[0]
    best_conf = max((v[2] for v in votes if v[0] == best_val and v[1] == best_ln), default=0.7)
    return FieldEvidence(value=best_val, confidence=min(1.0, best_conf), line_refs=[best_ln])


# Datum validator is intentionally light — we rely on LLM judgment; just filter absurd cases
_DATUM_BAD_RX = re.compile(r"\b(liable|warranty|copyright|all\s+rights\s+reserved)\b", re.I)


def _llm_extract_datum(lines: List[str], k_votes: int = 2):
    """LLM-driven extraction of geodetic/vertical datum string (e.g., NZGD2000, MSL).
    Returns FieldEvidence or None.
    """
    if not run_llm:
        return None
    provider = _get_llm_provider()
    if not provider:
        return None

    tags = _llm_tag_lines(lines) or {}

    def one_shot():
        allowed_lines = [i for i in range(1, min(40, len(lines)) + 1)
                         if tags.get(i, "OTHER") not in ("LEGAL_DISCLAIMER",)]
        snippet = "\n".join(f"C{str(i).zfill(2)} {lines[i-1]}" for i in allowed_lines)
        prompt = (
            "Extract the coordinate/vertical DATUM from this SEG-Y header (examples: WGS84, NZGD2000, ETRS89, MSL).\n"
            "Return strict JSON: {\"value\": <string or \"\" if unknown>, \"line_refs\": [<ints>], \"confidence\": <0..1>}\n"
            "Rules: prefer lines that contain the word DATUM or a known datum token; avoid legal text."
        )
        resp = run_llm(prompt + "\n\nHEADER:\n" + snippet, provider=provider)
        data = _parse_json_safely(resp if isinstance(resp, str) else str(resp))
        if not isinstance(data, dict):
            return None
        val = (data.get("value") or "").strip()
        lrefs = [int(x) for x in (data.get("line_refs") or []) if isinstance(x, (int, float, str))]
        conf = float(data.get("confidence") or 0.0)
        if not val or not lrefs:
            return None
        if _DATUM_BAD_RX.search(val):
            return None
        ln = int(lrefs[0])
        return val, ln, conf

    votes: List[Tuple[str, int, float]] = []
    for _ in range(max(1, k_votes)):
        try:
            r = one_shot()
            if r:
                votes.append(r)
        except Exception:
            continue

    if not votes:
        return None
    key_counts = Counter((v[0], v[1]) for v in votes)
    (best_val, best_ln), _ = key_counts.most_common(1)[0]
    best_conf = max((v[2] for v in votes if v[0] == best_val and v[1] == best_ln), default=0.65)
    return FieldEvidence(value=best_val, confidence=min(1.0, best_conf), line_refs=[best_ln])


_VESSEL_RX = re.compile(r"\b(MV|RV|R/V)\s+[A-Z0-9 \-]{3,}", re.I)


def _llm_extract_vessel(lines: List[str], k_votes: int = 2):
    """LLM-driven vessel extraction; prefers forms like 'MV XXX', 'R/V YYY'.\n    Returns FieldEvidence or None.
    """
    if not run_llm:
        return None
    provider = _get_llm_provider()
    if not provider:
        return None

    tags = _llm_tag_lines(lines) or {}

    def one_shot():
        allowed_lines = [i for i in range(1, min(40, len(lines)) + 1)
                         if tags.get(i, "OTHER") not in ("LEGAL_DISCLAIMER",)]
        snippet = "\n".join(f"C{str(i).zfill(2)} {lines[i-1]}" for i in allowed_lines)
        prompt = (
            "Extract the survey vessel name if present (e.g., 'MV XXX', 'R/V YYY'). If absent, return empty.\n"
            "Return strict JSON: {\"value\": <string or \"\" if unknown>, \"line_refs\": [<ints>], \"confidence\": <0..1>}\n"
        )
        resp = run_llm(prompt + "\n\nHEADER:\n" + snippet, provider=provider)
        data = _parse_json_safely(resp if isinstance(resp, str) else str(resp))
        if not isinstance(data, dict):
            return None
        val = (data.get("value") or "").strip()
        lrefs = [int(x) for x in (data.get("line_refs") or []) if isinstance(x, (int, float, str))]
        conf = float(data.get("confidence") or 0.0)
        if not val or not lrefs:
            return None
        if not _VESSEL_RX.search(val):
            # accept rare forms but with lower confidence
            if conf < 0.6:
                return None
        ln = int(lrefs[0])
        return val, ln, conf

    votes: List[Tuple[str, int, float]] = []
    for _ in range(max(1, k_votes)):
        try:
            r = one_shot()
            if r:
                votes.append(r)
        except Exception:
            continue

    if not votes:
        return None
    key_counts = Counter((v[0], v[1]) for v in votes)
    (best_val, best_ln), _ = key_counts.most_common(1)[0]
    best_conf = max((v[2] for v in votes if v[0] == best_val and v[1] == best_ln), default=0.65)
    return FieldEvidence(value=best_val, confidence=min(1.0, best_conf), line_refs=[best_ln])


# --- More LLM-first extractors (survey_name, area, geometry, source_type, receiver_type, acquisition_year) ---

_SURVEY_HINT_RX = re.compile(r"\b(SURVEY|PROJECT)\b", re.I)


def _llm_extract_survey_name(lines: List[str], k_votes: int = 2):
    if not run_llm:
        return None
    provider = _get_llm_provider()
    if not provider:
        return None
    tags = _llm_tag_lines(lines) or {}

    def one_shot():
        allowed = [i for i in range(1, min(40, len(lines)) + 1) if i <= 8 and tags.get(i, "OTHER") != "LEGAL_DISCLAIMER"]
        if not allowed:
            allowed = [i for i in range(1, min(40, len(lines)) + 1) if tags.get(i, "OTHER") != "LEGAL_DISCLAIMER"]
        snippet = "\n".join(f"C{str(i).zfill(2)} {lines[i-1]}" for i in allowed)
        prompt = (
            "Extract the SURVEY/PROJECT name from these SEG-Y header lines.\n"
            "Return strict JSON: {\"value\": <string or \\\"\\\" if unknown>, \"line_refs\": [<ints>], \"confidence\": <0..1>}"
        )
        resp = run_llm(prompt + "\n\nLINES:\n" + snippet, provider=provider)
        data = _parse_json_safely(resp if isinstance(resp, str) else str(resp))
        if not isinstance(data, dict):
            return None
        val = (data.get("value") or "").strip()
        lrefs = [int(x) for x in (data.get("line_refs") or []) if isinstance(x, (int, float, str))]
        conf = float(data.get("confidence") or 0.0)
        if not val or not lrefs:
            return None
        ln = int(lrefs[0])
        if ln > 15 and not _SURVEY_HINT_RX.search(lines[ln-1]):
            if conf < 0.7:
                return None
        return val, ln, conf

    votes: List[Tuple[str, int, float]] = []
    for _ in range(k_votes):
        try:
            r = one_shot()
            if r:
                votes.append(r)
        except Exception:
            continue
    if not votes:
        return None
    key_counts = Counter((v[0], v[1]) for v in votes)
    (best_val, best_ln), _ = key_counts.most_common(1)[0]
    best_conf = max((v[2] for v in votes if v[0] == best_val and v[1] == best_ln), default=0.75)
    return FieldEvidence(value=best_val, confidence=min(1.0, best_conf), line_refs=[best_ln])


def _llm_extract_area(lines: List[str], k_votes: int = 2):
    if not run_llm:
        return None
    provider = _get_llm_provider()
    if not provider:
        return None
    tags = _llm_tag_lines(lines) or {}

    def one_shot():
        allowed = [i for i in range(1, min(40, len(lines)) + 1) if tags.get(i, "OTHER") != "LEGAL_DISCLAIMER"]
        snippet = "\n".join(f"C{str(i).zfill(2)} {lines[i-1]}" for i in allowed)
        prompt = (
            "Extract the survey AREA/BASIN/LINE name (e.g., 'Great South Basin', 'Line PR4413').\n"
            "Prefer lines with AREA/LINE keywords; avoid legal text.\n"
            "Return strict JSON: {\"value\": <string or \\\"\\\" if unknown>, \"line_refs\": [<ints>], \"confidence\": <0..1>}"
        )
        resp = run_llm(prompt + "\n\nHEADER:\n" + snippet, provider=provider)
        data = _parse_json_safely(resp if isinstance(resp, str) else str(resp))
        if not isinstance(data, dict):
            return None
        val = (data.get("value") or "").strip()
        lrefs = [int(x) for x in (data.get("line_refs") or []) if isinstance(x, (int, float, str))]
        conf = float(data.get("confidence") or 0.0)
        if not val or not lrefs:
            return None
        return val, int(lrefs[0]), conf

    votes: List[Tuple[str, int, float]] = []
    for _ in range(k_votes):
        try:
            r = one_shot()
            if r:
                votes.append(r)
        except Exception:
            continue
    if not votes:
        return None
    key_counts = Counter((v[0], v[1]) for v in votes)
    (best_val, best_ln), _ = key_counts.most_common(1)[0]
    best_conf = max((v[2] for v in votes if v[0] == best_val and v[1] == best_ln), default=0.7)
    return FieldEvidence(value=best_val, confidence=min(1.0, best_conf), line_refs=[best_ln])


_GEOM_ALLOWED = {"2D", "3D"}
_ACQ_ALLOWED = {"TOWED STREAMER", "OBN", "OBC", "LAND"}


def _llm_extract_geometry(lines: List[str], k_votes: int = 2):
    if not run_llm:
        return None
    provider = _get_llm_provider()
    if not provider:
        return None
    tags = _llm_tag_lines(lines) or {}

    def one_shot():
        allowed = [i for i in range(1, min(40, len(lines)) + 1) if tags.get(i, "OTHER") != "LEGAL_DISCLAIMER"]
        snippet = "\n".join(f"C{str(i).zfill(2)} {lines[i-1]}" for i in allowed)
        prompt = (
            "Identify geometry and environment (one of): 2D/3D + (TOWED STREAMER | OBN | OBC | LAND).\n"
            "Return strict JSON: {\"value\": <string like '3D OBN' or empty>, \"line_refs\": [<ints>], \"confidence\": <0..1>}"
        )
        resp = run_llm(prompt + "\n\nHEADER:\n" + snippet, provider=provider)
        data = _parse_json_safely(resp if isinstance(resp, str) else str(resp))
        if not isinstance(data, dict):
            return None
        val = (data.get("value") or "").strip().upper()
        lrefs = [int(x) for x in (data.get("line_refs") or []) if isinstance(x, (int, float, str))]
        conf = float(data.get("confidence") or 0.0)
        if not val or not lrefs:
            return None
        ok = any(g in val for g in _GEOM_ALLOWED) and any(a in val for a in _ACQ_ALLOWED)
        if not ok and conf < 0.75:
            return None
        return val.title(), int(lrefs[0]), conf

    votes: List[Tuple[str, int, float]] = []
    for _ in range(k_votes):
        try:
            r = one_shot()
            if r:
                votes.append(r)
        except Exception:
            continue
    if not votes:
        return None
    key_counts = Counter((v[0], v[1]) for v in votes)
    (best_val, best_ln), _ = key_counts.most_common(1)[0]
    best_conf = max((v[2] for v in votes if v[0] == best_val and v[1] == best_ln), default=0.75)
    return FieldEvidence(value=best_val, confidence=min(1.0, best_conf), line_refs=[best_ln])


_SOURCE_ALLOWED = {"AIR GUN", "AIRGUN", "VIBROSEIS", "DYNAMITE"}


def _llm_extract_source_type(lines: List[str], k_votes: int = 2):
    if not run_llm:
        return None
    provider = _get_llm_provider()
    if not provider:
        return None
    tags = _llm_tag_lines(lines) or {}

    def one_shot():
        allowed = [i for i in range(1, min(40, len(lines)) + 1) if tags.get(i, "OTHER") != "LEGAL_DISCLAIMER"]
        snippet = "\n".join(f"C{str(i).zfill(2)} {lines[i-1]}" for i in allowed)
        prompt = (
            "Extract SOURCE TYPE: one of AIR GUN/AIRGUN, VIBROSEIS, DYNAMITE.\n"
            "Return strict JSON: {\"value\": <string or empty>, \"line_refs\": [<ints>], \"confidence\": <0..1>}"
        )
        resp = run_llm(prompt + "\n\nHEADER:\n" + snippet, provider=provider)
        data = _parse_json_safely(resp if isinstance(resp, str) else str(resp))
        if not isinstance(data, dict):
            return None
        val = (data.get("value") or "").strip().upper()
        lrefs = [int(x) for x in (data.get("line_refs") or []) if isinstance(x, (int, float, str))]
        conf = float(data.get("confidence") or 0.0)
        if not val or not lrefs:
            return None
        if all(tok not in val for tok in _SOURCE_ALLOWED) and conf < 0.7:
            return None
        return val.title().replace(" ", ""), int(lrefs[0]), conf

    votes: List[Tuple[str, int, float]] = []
    for _ in range(k_votes):
        try:
            r = one_shot()
            if r:
                votes.append(r)
        except Exception:
            continue
    if not votes:
        return None
    key_counts = Counter((v[0], v[1]) for v in votes)
    (best_val, best_ln), _ = key_counts.most_common(1)[0]
    best_conf = max((v[2] for v in votes if v[0] == best_val and v[1] == best_ln), default=0.7)
    return FieldEvidence(value=best_val, confidence=min(1.0, best_conf), line_refs=[best_ln])


_RECEIVER_ALLOWED = {"STREAMER", "GEOPHONE", "HYDROPHONE", "NODE"}


def _llm_extract_receiver_type(lines: List[str], k_votes: int = 2):
    if not run_llm:
        return None
    provider = _get_llm_provider()
    if not provider:
        return None
    tags = _llm_tag_lines(lines) or {}

    def one_shot():
        allowed = [i for i in range(1, min(40, len(lines)) + 1) if tags.get(i, "OTHER") != "LEGAL_DISCLAIMER"]
        snippet = "\n".join(f"C{str(i).zfill(2)} {lines[i-1]}" for i in allowed)
        prompt = (
            "Extract RECEIVER TYPE: one of STREAMER, GEOPHONE, HYDROPHONE, NODE.\n"
            "Return strict JSON: {\"value\": <string or empty>, \"line_refs\": [<ints>], \"confidence\": <0..1>}"
        )
        resp = run_llm(prompt + "\n\nHEADER:\n" + snippet, provider=provider)
        data = _parse_json_safely(resp if isinstance(resp, str) else str(resp))
        if not isinstance(data, dict):
            return None
        val = (data.get("value") or "").strip().upper()
        lrefs = [int(x) for x in (data.get("line_refs") or []) if isinstance(x, (int, float, str))]
        conf = float(data.get("confidence") or 0.0)
        if not val or not lrefs:
            return None
        if all(tok not in val for tok in _RECEIVER_ALLOWED) and conf < 0.7:
            return None
        return val.title(), int(lrefs[0]), conf

    votes: List[Tuple[str, int, float]] = []
    for _ in range(k_votes):
        try:
            r = one_shot()
            if r:
                votes.append(r)
        except Exception:
            continue
    if not votes:
        return None
    key_counts = Counter((v[0], v[1]) for v in votes)
    (best_val, best_ln), _ = key_counts.most_common(1)[0]
    best_conf = max((v[2] for v in votes if v[0] == best_val and v[1] == best_ln), default=0.7)
    return FieldEvidence(value=best_val, confidence=min(1.0, best_conf), line_refs=[best_ln])


def _llm_extract_acquisition_year(lines: List[str], k_votes: int = 2):
    if not run_llm:
        return None
    provider = _get_llm_provider()
    if not provider:
        return None
    tags = _llm_tag_lines(lines) or {}

    def one_shot():
        allowed = [i for i in range(1, min(40, len(lines)) + 1) if tags.get(i, "OTHER") != "LEGAL_DISCLAIMER"]
        snippet = "\n".join(f"C{str(i).zfill(2)} {lines[i-1]}" for i in allowed)
        prompt = (
            "Extract the ACQUISITION/RECORDED year (YYYY) from the header.\n"
            "Avoid legal text and examples. Return strict JSON: {\"value\": <int or 0 if unknown>, \"line_refs\": [<ints>], \"confidence\": <0..1>}"
        )
        resp = run_llm(prompt + "\n\nHEADER:\n" + snippet, provider=provider)
        data = _parse_json_safely(resp if isinstance(resp, str) else str(resp))
        if not isinstance(data, dict):
            return None
        try:
            val = int(data.get("value") or 0)
        except Exception:
            val = 0
        lrefs = [int(x) for x in (data.get("line_refs") or []) if isinstance(x, (int, float, str))]
        conf = float(data.get("confidence") or 0.0)
        if not val or not (1900 <= val <= 2099) or not lrefs:
            return None
        return val, int(lrefs[0]), conf

    votes: List[Tuple[int, int, float]] = []
    for _ in range(k_votes):
        try:
            r = one_shot()
            if r:
                votes.append(r)
        except Exception:
            continue
    if not votes:
        return None
    key_counts = Counter((v[0], v[1]) for v in votes)
    (best_val, best_ln), _ = key_counts.most_common(1)[0]
    best_conf = max((v[2] for v in votes if v[0] == best_val and v[1] == best_ln), default=0.75)
    return FieldEvidence(value=best_val, confidence=min(1.0, best_conf), line_refs=[best_ln])


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

    # Survey name: LLM-first with fallback to SURVEY/PROJECT regex in first lines
    fe_survey = None
    try:
        fe_survey = _llm_extract_survey_name(lines)
    except Exception:
        fe_survey = None
    if fe_survey:
        hj.survey_name = fe_survey
    else:
        res = _match_on_lines(
            lines[:5],
            [re.compile(r"SURVEY\s*[:=]?\s*([A-Z0-9_\- ]{3,})"), re.compile(r"PROJECT\s*[:=]?\s*([A-Z0-9_\- ]{3,})")],
        )
        if res:
            line_no, m = res
            val = m.group(1).strip().rstrip(" .")
            hj.survey_name = FieldEvidence(value=val, confidence=0.9, line_refs=[line_no])

    # Area: LLM-first then fallback to AREA regex
    fe_area = None
    try:
        fe_area = _llm_extract_area(lines)
    except Exception:
        fe_area = None
    if fe_area:
        hj.area = FieldEvidence(value=fe_area.value.title(), confidence=fe_area.confidence, line_refs=fe_area.line_refs)
    else:
        res = _match_on_lines(lines[:6], [re.compile(r"AREA\s*[:=]?\s*([A-Z0-9_\- ]{3,})")])
        if res:
            line_no, m = res
            val = m.group(1).strip().rstrip(" .")
            hj.area = FieldEvidence(value=val.title(), confidence=0.85, line_refs=[line_no])

    # Contractor: prefer LLM extraction with line tagging; fall back to regex
    fe_contractor = None
    try:
        fe_contractor = _llm_extract_contractor(lines)
    except Exception:
        fe_contractor = None

    if fe_contractor:
        hj.contractor = fe_contractor
    else:
        res = _match_on_lines(
            lines,
            [
                re.compile(r"CONTRACTOR\s*[:=]?\s*([A-Z0-9 &_\-]{3,})"),
                re.compile(r"COMPANY\s*[:=]?\s*([A-Z0-9 &_\-]{3,})"),
            ],
        )
        if res:
            line_no, m = res
            raw = m.group(1).strip().rstrip(" .")
            if _validate_contractor_candidate(raw, line_no):
                hj.contractor = FieldEvidence(value=raw.title(), confidence=0.85, line_refs=[line_no])

    # Company (client/data owner): prefer LLM, then fallback to regex; optional field
    if hasattr(hj, "company"):
        fe_company = None
        try:
            fe_company = _llm_extract_company(lines)
        except Exception:
            fe_company = None
        if fe_company:
            hj.company = fe_company
        else:
            res = _match_on_lines(
                lines,
                [re.compile(r"COMPANY\s*[:=]?\s*([A-Z0-9 &_\-]{3,})")],
            )
            if res:
                line_no, m = res
                raw = m.group(1).strip().rstrip(" .")
                if _validate_contractor_candidate(raw, line_no):
                    hj.company = FieldEvidence(value=raw.title(), confidence=0.8, line_refs=[line_no])

    # Acquisition year: LLM-first then fallback regex
    fe_year = None
    try:
        fe_year = _llm_extract_acquisition_year(lines)
    except Exception:
        fe_year = None
    if fe_year:
        hj.acquisition_year = fe_year
    else:
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
    fe_geom = None
    try:
        fe_geom = _llm_extract_geometry(lines)
    except Exception:
        fe_geom = None
    if fe_geom:
        hj.geometry = fe_geom
    else:
        res = _match_on_lines(lines, [re.compile(r"\b(2D|3D)\b.*(TOWED\s+STREAMER|OBN|OBC|LAND)")])
        if res:
            line_no, m = res
            hj.geometry = FieldEvidence(value=m.group(0).title(), confidence=0.8, line_refs=[line_no])

    # Source type
    fe_src = None
    try:
        fe_src = _llm_extract_source_type(lines)
    except Exception:
        fe_src = None
    if fe_src:
        hj.source_type = fe_src
    else:
        res = _match_on_lines(lines, [re.compile(r"AIR\s*GUN|AIRGUN|VIBROSEIS|DYNAMITE")])
        if res:
            line_no, m = res
            hj.source_type = FieldEvidence(value=m.group(0).title().replace(" ", ""), confidence=0.8, line_refs=[line_no])

    # Receiver type
    fe_rcv = None
    try:
        fe_rcv = _llm_extract_receiver_type(lines)
    except Exception:
        fe_rcv = None
    if fe_rcv:
        hj.receiver_type = fe_rcv
    else:
        res = _match_on_lines(lines, [re.compile(r"STREAMER|GEOPHONE|HYDROPHONE|NODE")])
        if res:
            line_no, m = res
            hj.receiver_type = FieldEvidence(value=m.group(0).title(), confidence=0.8, line_refs=[line_no])

    # Datum and SRD
    fe_datum = None
    try:
        fe_datum = _llm_extract_datum(lines)
    except Exception:
        fe_datum = None
    if fe_datum:
        hj.datum = fe_datum
    else:
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
    fe_vessel = None
    try:
        fe_vessel = _llm_extract_vessel(lines)
    except Exception:
        fe_vessel = None
    if fe_vessel:
        hj.vessel = fe_vessel
    else:
        res = _match_on_lines(lines, [re.compile(r"\b(MV|RV|R/V)\s+[A-Z0-9 \-]{3,}")])
        if res:
            line_no, m = res
            hj.vessel = FieldEvidence(value=m.group(0).title(), confidence=0.7, line_refs=[line_no])

    # Notes: If we matched nothing else, or to aggregate
    # For now leave None; could combine unmatched C-lines later.

    return hj
