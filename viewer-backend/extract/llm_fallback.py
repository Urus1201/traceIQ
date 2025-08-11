from __future__ import annotations

import traceback
from typing import Dict, List, Protocol, Tuple, Any

from app.schemas import FieldEvidence, HeaderJSON


class LLMProvider(Protocol):
    def infer(self, prompt: str) -> dict:
        ...


PROMPT_TEMPLATE = (
    "You are parsing a SEG-Y textual (EBCDIC/ASCII) header: 40 lines of 80 chars.\n"
    "Return STRICT JSON. Top-level key must be 'header' and each entry must match this schema: \n"
    "{schema}\n\n"
    "For each present field include:\n"
    "- 'value' (string/number)\n"
    "- 'confidence' in [0,1]\n"
    "- 'line_refs' as an array of 1-based integers (1..40).\n"
    "If unknown, omit the field entirely. Do NOT invent keys not in the schema.\n\n"
    "Lines (numbered):\n{lines}\n"
)


def _format_lines(lines: List[str]) -> str:
    parts: List[str] = []
    for i, line in enumerate(lines, start=1):
        parts.append(f"{i}: {line.rstrip()}\n")
    return "".join(parts)


def _allowed_fields() -> List[str]:
    # Only accept fields that exist on HeaderJSON to avoid schema drift
    return list(HeaderJSON.model_fields.keys())


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def run_llm(lines: List[str], provider: LLMProvider) -> Dict[str, FieldEvidence]:
    schema_json = HeaderJSON.model_json_schema()
    prompt = PROMPT_TEMPLATE.format(schema=schema_json, lines=_format_lines(lines))
    try:
        raw = provider.infer(prompt)
    except Exception:
        # If provider fails (network, auth, bad deployment), fall back silently
        return {}
    out: Dict[str, FieldEvidence] = {}

    try:
        header_obj: Dict[str, Any] = raw.get("header", {}) if isinstance(raw, dict) else {}
        allowed = set(_allowed_fields())
        for k, v in header_obj.items():
            if k not in allowed or not isinstance(v, dict):
                continue
            conf = v.get("confidence", 0.0)
            try:
                conf = float(conf)
            except Exception:
                conf = 0.0
            fe = FieldEvidence(
                value=v.get("value"),
                confidence=_clamp01(conf),
                line_refs=list(v.get("line_refs", []) or []),
            )
            out[k] = fe
    except Exception:
        # Treat failures as no fields
        return {}
    return out


def _equalish(a: Any, b: Any) -> bool:
    """Loose equality: numeric within tolerance; strings case/space-insensitive."""
    # Exact fast-path
    if a == b:
        return True
    # Try numeric
    try:
        fa, fb = float(a), float(b)
        # relative 1% or absolute 0.001 tolerance
        return abs(fa - fb) <= max(0.001, 0.01 * max(abs(fa), abs(fb)))
    except Exception:
        pass
    # Try string normalize
    try:
        sa, sb = str(a).strip().upper(), str(b).strip().upper()
        return sa == sb
    except Exception:
        return False


def merge_with_confidence(
    baseline: Dict[str, FieldEvidence],
    llm: Dict[str, FieldEvidence],
) -> Tuple[Dict[str, FieldEvidence], List[dict]]:
    """Merge two sources with confidence voting, returning fields and provenance list.

    Rules:
    - If only one source has the field, take it; provenance source=baseline/llm.
    - If both have value and they are equal (numeric tolerance; case-insensitive for text),
      choose the higher-confidence value, union line refs, and boost chosen confidence slightly.
    - If both have value but differ, choose the higher confidence and apply a small penalty.
    """
    merged: Dict[str, FieldEvidence] = {}
    provenance: List[dict] = []

    keys = set(baseline.keys()) | set(llm.keys())

    for k in sorted(keys):
        b = baseline.get(k)
        l = llm.get(k)

        if b and not l:
            merged[k] = b
            provenance.append(
                {
                    "field": k,
                    "source": "baseline",
                    "baseline_conf": b.confidence,
                    "chosen_conf": b.confidence,
                    "line_refs": b.line_refs,
                }
            )
            continue
        if l and not b:
            merged[k] = l
            provenance.append(
                {
                    "field": k,
                    "source": "llm",
                    "llm_conf": l.confidence,
                    "chosen_conf": l.confidence,
                    "line_refs": l.line_refs,
                }
            )
            continue
        if not b and not l:
            continue

        # Both present
        vb = b.value if b else None
        vl = l.value if l else None
        if _equalish(vb, vl):
            # Agree: take higher confidence, union line refs, boost slightly
            bc = b.confidence if b else 0.0
            lc = l.confidence if l else 0.0
            chosen_src = "baseline" if bc >= lc else "llm"
            chosen = b if bc >= lc else l  # type: ignore[assignment]
            line_refs = sorted(set((b.line_refs if b else []) + (l.line_refs if l else [])))
            boosted_conf = _clamp01((bc + lc) / 2.0 + 0.10)
            merged[k] = FieldEvidence(value=chosen.value, confidence=boosted_conf, line_refs=line_refs)  # type: ignore[arg-type]
            provenance.append(
                {
                    "field": k,
                    "source": "merged_agree",
                    "baseline_conf": bc,
                    "llm_conf": lc,
                    "chosen_conf": boosted_conf,
                    "line_refs": line_refs,
                }
            )
        else:
            # Disagree: take higher confidence with small penalty
            bc = b.confidence if b else 0.0
            lc = l.confidence if l else 0.0
            if bc >= lc:
                penalized = _clamp01(bc - 0.05)
                merged[k] = FieldEvidence(value=b.value, confidence=penalized, line_refs=b.line_refs)  # type: ignore[arg-type]
                provenance.append(
                    {
                        "field": k,
                        "source": "baseline",
                        "baseline_conf": bc,
                        "llm_conf": lc,
                        "chosen_conf": penalized,
                        "line_refs": b.line_refs,
                    }
                )
            else:
                penalized = _clamp01(lc - 0.05)
                merged[k] = FieldEvidence(value=l.value, confidence=penalized, line_refs=l.line_refs)  # type: ignore[arg-type]
                provenance.append(
                    {
                        "field": k,
                        "source": "llm",
                        "baseline_conf": bc,
                        "llm_conf": lc,
                        "chosen_conf": penalized,
                        "line_refs": l.line_refs,
                    }
                )

    return merged, provenance
