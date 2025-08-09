from __future__ import annotations

from typing import Dict, List, Tuple

from extract.evidence import FieldEvidence


def highlight_value(raw_line: str, span: Tuple[int, int]) -> str:
    """Wrap the given [start,end) span with ⟦ ⟧ markers for UI highlighting.
    If the span is invalid or out of bounds, return the raw line unchanged.
    """
    start, end = span
    if not (0 <= start <= end <= len(raw_line)):
        return raw_line
    return raw_line[:start] + "⟦" + raw_line[start:end] + "⟧" + raw_line[end:]


def gather_line_highlights(evidences: Dict[str, FieldEvidence], lines: List[str]) -> Dict[int, List[Tuple[int, int]]]:
    """Collect highlight spans per 1-based line number from evidences.
    For each evidence, all raw_spans (if any) are associated to all its line_refs.
    Spans are clipped to the line length and deduplicated.
    Returns: {lineno: [(start,end), ...]}
    """
    result: Dict[int, List[Tuple[int, int]]] = {}
    n = len(lines)

    for _name, ev in evidences.items():
        line_refs = ev.get("line_refs", []) or []
        spans = ev.get("raw_spans", []) or []
        for lineno in line_refs:
            if not (1 <= lineno <= n):
                continue
            line = lines[lineno - 1]
            L = len(line)
            for (s, e) in spans:
                s2 = max(0, min(s, L))
                e2 = max(s2, min(e, L))
                if s2 >= e2:
                    continue
                result.setdefault(lineno, [])
                if (s2, e2) not in result[lineno]:
                    result[lineno].append((s2, e2))

    # Optionally sort spans by start
    for lineno in result:
        result[lineno].sort(key=lambda t: (t[0], t[1]))

    return result
