from __future__ import annotations

import re
from typing import Dict, List

# Simple typo normalization used only for matching, not for display
TYPO_MAP: Dict[str, str] = {
    "INTERNAL": "INTERVAL",  # SAMPLE INTERNAL -> SAMPLE INTERVAL
    "RECOVEY": "RECOVERY",
    "MEASURMENT": "MEASUREMENT",
}

# Characters considered as token separators (anything non A-Z0-9 after uppercasing)
SEP_RE = re.compile(r"[^A-Z0-9]+")
WS_RE = re.compile(r"\s+")


def _collapse_spaces(s: str) -> str:
    # Normalize whitespace to single space; keep leading/trailing spaces trimmed for canonical
    return WS_RE.sub(" ", s.strip())


def _apply_typos_to_tokens(tokens: List[str]) -> List[str]:
    fixed: List[str] = []
    for t in tokens:
        fixed.append(TYPO_MAP.get(t, t))
    return fixed


def normalize_line(raw: str) -> Dict[str, object]:
    """
    Normalize a single 80-char textual header line.
    Returns dict with:
      - raw: original string (unchanged)
      - canonical: uppercased, collapsed spaces
      - tokens: split on spaces/punctuation from canonical (no typo fixes)
      - match_canonical: canonical with typos fixed for matching only
      - match_tokens: tokens derived from match_canonical (typo-fixed)
    """
    # Uppercase for robust matching and collapse spaces
    canonical = _collapse_spaces(raw.upper())
    # Tokenize on non-alphanumeric
    tokens = [t for t in SEP_RE.split(canonical) if t]

    # Apply typo fixes only for matching views
    match_tokens = _apply_typos_to_tokens(tokens)
    # Rebuild a canonical-like string from tokens so pattern searchers can use substring checks
    match_canonical = " ".join(match_tokens)

    return {
        "raw": raw,
        "canonical": canonical,
        "tokens": tokens,
        "match_canonical": match_canonical,
        "match_tokens": match_tokens,
    }


def normalize_lines(lines: List[str]) -> List[Dict[str, object]]:
    """Normalize list of textual header lines and add 1-based line numbers."""
    out: List[Dict[str, object]] = []
    for i, line in enumerate(lines, start=1):
        rec = normalize_line(line)
        rec["lineno"] = i
        out.append(rec)
    return out
