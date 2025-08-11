from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

# -----------------------------
# Token patterns and aliases
# -----------------------------

DATUM_ALIASES: Dict[str, List[str]] = {
    "WGS84": ["WGS84", "WGS 84", "WGS-84", "WORLD GEODETIC SYSTEM 1984"],
    "NAD27": ["NAD27", "N.A.D. 27", "NAD 27", "NORTH AMERICAN DATUM 1927"],
    "NAD83": ["NAD83", "N.A.D. 83", "NAD 83", "NORTH AMERICAN DATUM 1983"],
    "ED50": ["ED50", "EUROPEAN DATUM 1950", "ED 50", "ED-50"],
    "ETRS89": ["ETRS89", "ETRF89", "ETRF2000", "ETRS 89", "ETRS-89"],
}

UTM_WORDS = [r"\bUTM\b", r"UNIVERSAL\s+TRANSVERSE\s+MERCATOR"]
ZONE_RE = re.compile(r"\b([1-9]|[1-5]\d|60)\s*([NS])?\b", re.IGNORECASE)
HEMI_N_WORDS = [r"\bN\b", r"\bNORTH\b", r"\bNORTHERN\b"]
HEMI_S_WORDS = [r"\bS\b", r"\bSOUTH\b", r"\bSOUTHERN\b"]
UNITS_M_WORDS = [r"\bM\b", r"\bMETER\b", r"\bMETERS\b", r"\bMETRE\b", r"\bMETRES\b"]
UNITS_FT_WORDS = [r"\bFT\b", r"\bFEET\b", r"\bFOOT\b", r"US\s+SURVEY\s+FOOT"]
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

# Regions are extremely lightweight; we use them only for priors
EUROPE_HINTS = [
    "NORTH SEA",
    "NORWAY",
    "UK",
    "UNITED KINGDOM",
    "GERMANY",
    "FRANCE",
    "NETHERLANDS",
    "DENMARK",
    "POLAND",
]
NA_HINTS = [
    "GULF OF MEXICO",
    "USA",
    "UNITED STATES",
    "CANADA",
    "MEXICO",
]
ME_INDIA_HINTS = [
    "KUWAIT",
    "KSA",
    "SAUDI ARABIA",
    "UAE",
    "OMAN",
    "INDIA",
]


@dataclass
class MatchedToken:
    token: str
    weight: float
    line_idx: int
    span: Optional[Tuple[int, int]] = None
    source: str = "text"


@dataclass
class ExtractedFeatures:
    utm: bool = False
    zone: Optional[int] = None
    hemi: Optional[str] = None  # 'N' or 'S'
    datum: Optional[str] = None  # canonical key from DATUM_ALIASES
    units: Optional[str] = None  # 'm', 'ft', or None
    year: Optional[int] = None
    region: Optional[str] = None  # 'europe', 'na', 'me_india'
    matched_keywords: List[MatchedToken] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _normalize(s: str) -> str:
    # Uppercase, collapse whitespace, strip punctuation edges (keep slashes and digits)
    s = s.upper()
    s = re.sub(r"[\t\r]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_features(lines: List[str]) -> ExtractedFeatures:
    feats = ExtractedFeatures()

    for idx, raw in enumerate(lines or []):
        line = _normalize(str(raw))
        if not line:
            continue

        # UTM keyword
        for w in UTM_WORDS:
            if re.search(w, line):
                feats.utm = True
                feats.matched_keywords.append(MatchedToken("UTM", 2.0, idx))
                break

        # Zone + hemisphere patterns (look for UTM context but don't require it)
        for m in ZONE_RE.finditer(line):
            try:
                z = int(m.group(1))
            except ValueError:
                continue
            if 1 <= z <= 60:
                # prefer first plausible zone if none set
                if feats.zone is None:
                    feats.zone = z
                    feats.matched_keywords.append(
                        MatchedToken(f"ZONE {z}{m.group(2) or ''}", 3.0, idx, m.span())
                    )
                # Hemisphere capture
                if m.group(2):
                    hemi = m.group(2).upper()
                    feats.hemi = "N" if hemi == "N" else "S"
        # Words that specify hemisphere without a zone
        if any(re.search(p, line) for p in HEMI_N_WORDS):
            feats.hemi = "N"
            feats.matched_keywords.append(MatchedToken("HEMI N", 1.0, idx))
        if any(re.search(p, line) for p in HEMI_S_WORDS):
            feats.hemi = "S"
            feats.matched_keywords.append(MatchedToken("HEMI S", 1.0, idx))

        # Datum aliases
        for canon, aliases in DATUM_ALIASES.items():
            for a in aliases:
                if a in line:
                    # If multiple datums appear, keep first but note ambiguity in notes
                    if feats.datum and feats.datum != canon:
                        feats.notes.append(f"Multiple datums mentioned: {feats.datum} and {canon}")
                    if not feats.datum:
                        feats.datum = canon
                    feats.matched_keywords.append(MatchedToken(canon, 4.0, idx))
                    break

        # Units
        if any(re.search(p, line) for p in UNITS_M_WORDS):
            feats.units = "m"
            feats.matched_keywords.append(MatchedToken("UNITS M", 0.5, idx))
        if any(re.search(p, line) for p in UNITS_FT_WORDS):
            feats.units = "ft"
            feats.matched_keywords.append(MatchedToken("UNITS FT", -2.0, idx))

        # Year
        ym = YEAR_RE.search(line)
        if ym and feats.year is None:
            feats.year = int(ym.group(1))
            feats.matched_keywords.append(MatchedToken(f"YEAR {feats.year}", 0.5, idx, ym.span()))

        # Region hints
        l = line
        if any(h in l for h in EUROPE_HINTS):
            feats.region = "europe"
            feats.matched_keywords.append(MatchedToken("REGION EUROPE", 0.5, idx))
        elif any(h in l for h in NA_HINTS):
            feats.region = "na"
            feats.matched_keywords.append(MatchedToken("REGION NA", 0.5, idx))
        elif any(h in l for h in ME_INDIA_HINTS):
            feats.region = "me_india"
            feats.matched_keywords.append(MatchedToken("REGION ME_INDIA", 0.5, idx))

    return feats


__all__ = [
    "MatchedToken",
    "ExtractedFeatures",
    "extract_features",
    "DATUM_ALIASES",
]
