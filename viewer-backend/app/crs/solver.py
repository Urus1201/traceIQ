from __future__ import annotations

import math
from dataclasses import dataclass
import os
from typing import Any, Dict, List, Optional, Tuple

from .heuristics import extract_features, ExtractedFeatures
from .epsg_catalog import utm_epsg, utm_label, FAMILIES
from .diagnostics import pack_matched


@dataclass
class Candidate:
    epsg: int
    label: str
    family: str  # WGS84, NAD83, etc.
    zone: Optional[int]
    hemi: Optional[str]


DEFAULT_WEIGHTS = {
    "UTM": 2.0,
    "ZONE": 3.0,
    "DATUM": 4.0,
    "HEMI": 2.0,
    "UNITS_M": 1.0,
    "UNITS_FT": -2.0,
    "NO_DATUM": -1.0,
    "AMBIG_DATUM": -2.0,
}


def _load_weights() -> dict:
    w = dict(DEFAULT_WEIGHTS)
    # Allow overrides like CRS_WEIGHT_DATUM=5.0
    for k in list(w.keys()):
        env_key = f"CRS_WEIGHT_{k}"
        if env_key in os.environ:
            try:
                w[k] = float(os.environ[env_key])
            except ValueError:
                pass
    return w


WEIGHTS = _load_weights()


def _softmax(xs: List[float], temperature: float = 1.0) -> List[float]:
    if not xs:
        return []
    mx = max(xs)
    exps = [math.exp((x - mx) / max(temperature, 1e-6)) for x in xs]
    s = sum(exps)
    if s <= 0:
        return [1.0 / len(xs)] * len(xs)
    return [e / s for e in exps]


def _vintage_prior(family: str, year: Optional[int], region: Optional[str]) -> Tuple[float, List[str]]:
    s = 0.0
    reasons: List[str] = []
    if year is not None:
        if year <= 1975:
            if family == "NAD27":
                s += 2.0; reasons.append("vintage<=1975 favors NAD27")
            if family == "WGS84":
                s -= 2.0; reasons.append("vintage<=1975 penalizes WGS84")
        elif 1976 <= year <= 1990:
            if family in ("NAD83", "ED50"):
                s += 1.0; reasons.append("1976-1990 favors NAD83/ED50")
        elif year >= 1991:
            if family in ("WGS84", "ETRS89"):
                s += 2.0; reasons.append(">=1991 favors WGS84/ETRS89")
            if family == "NAD27":
                s -= 2.0; reasons.append(">=1991 penalizes NAD27")
    # Region hints
    if region == "na":
        if family == "NAD83":
            s += 1.0; reasons.append("region NA favors NAD83")
    elif region == "europe":
        if family in ("ETRS89", "ED50"):
            s += 1.0; reasons.append("region Europe favors ETRS89/ED50")
    elif region == "me_india":
        if family == "WGS84":
            s += 1.0; reasons.append("region ME/India favors WGS84")
    return s, reasons


def _generate_candidates(feats: ExtractedFeatures) -> List[Candidate]:
    cands: List[Candidate] = []
    # Generate UTM-based candidates across datum families we support
    zone = feats.zone or 32  # default to a common zone if absent
    hemi_options = [feats.hemi] if feats.hemi in ("N", "S") else ["N", "S"]

    families = list(FAMILIES.keys())
    if feats.datum in families:
        # prioritize detected datum by ordering, still include others for ambiguity resolution
        families = [feats.datum] + [f for f in families if f != feats.datum]

    for fam in families:
        for h in hemi_options:
            epsg = utm_epsg(fam, zone, h)
            if epsg is None:
                continue
            label = utm_label(fam, zone, h)
            cands.append(Candidate(epsg=epsg, label=label, family=fam, zone=zone, hemi=h))
    return cands


def solve_crs(
    lines: List[str],
    bin_header: Optional[Dict[str, Any]] = None,
    trace_stats: Optional[Dict[str, Any]] = None,
    temperature: float = 1.0,
) -> Dict[str, Any]:
    feats = extract_features(lines)

    diagnostics: Dict[str, Any] = {
        "matched_keywords": pack_matched(feats.matched_keywords),
        "conflicts": [],
        "penalties": [],
        "notes": list(feats.notes),
    }

    cands = _generate_candidates(feats)
    scores: List[float] = []
    cand_infos: List[Dict[str, Any]] = []

    # Handle ambiguity penalties
    if feats.zone is not None and feats.datum is None:
        diagnostics["penalties"].append({"reason": "zone present but no datum", "delta": WEIGHTS["NO_DATUM"]})
    if any("Multiple datums" in n for n in feats.notes):
        diagnostics["conflicts"].append("datum ambiguity")
        diagnostics["penalties"].append({"reason": "multiple datums", "delta": WEIGHTS["AMBIG_DATUM"]})

    for c in cands:
        s = 0.0
        reasons: List[str] = []
        penalties_local: List[Dict[str, Any]] = []

        # UTM keyword
        if feats.utm:
            s += WEIGHTS["UTM"]; reasons.append("found 'UTM'")
        # Zone
        if feats.zone is not None and c.zone == feats.zone:
            s += WEIGHTS["ZONE"]; reasons.append(f"zone {feats.zone}")
        # Datum
        if feats.datum == c.family:
            s += WEIGHTS["DATUM"]; reasons.append(f"datum '{c.family}'")
        # Hemisphere
        if feats.hemi and c.hemi == feats.hemi:
            s += WEIGHTS["HEMI"]; reasons.append(f"hemisphere '{feats.hemi}'")
        # Units
        units = (trace_stats or {}).get("units") or feats.units
        if units == "m":
            s += WEIGHTS["UNITS_M"]; reasons.append("meters unit")
        elif units == "ft":
            # Penalize UTM with feet
            s += WEIGHTS["UNITS_FT"]; penalties_local.append({"reason": "feet with UTM", "delta": WEIGHTS["UNITS_FT"]})
            diagnostics["conflicts"].append("feet with UTM")

        # Global ambiguity penalties
        if feats.zone is not None and feats.datum is None:
            s += WEIGHTS["NO_DATUM"]
        if any("Multiple datums" in n for n in feats.notes):
            s += WEIGHTS["AMBIG_DATUM"]

        # Vintage + region priors
        dv, dv_reasons = _vintage_prior(c.family, feats.year, feats.region)
        s += dv; reasons.extend(dv_reasons)

        scores.append(s)
        cand_infos.append({
            "candidate": c,
            "reasons": reasons,
            "penalties": penalties_local,
        })

    probs = _softmax(scores, temperature=temperature)

    # Build response
    candidates_out: List[Dict[str, Any]] = []
    for info, p in zip(cand_infos, probs):
        c: Candidate = info["candidate"]
        candidates_out.append({
            "epsg": c.epsg,
            "label": c.label,
            "p": round(float(p), 4),
            "reasons": info["reasons"],
        })

    # Ambiguity note
    if candidates_out:
        top1 = max(candidates_out, key=lambda x: x["p"])
        if top1["p"] < 0.7:
            diagnostics.setdefault("notes", []).append("ambiguous; consider manual confirm")

    return {
        "candidates": sorted(candidates_out, key=lambda x: -x["p"])[:10],
        "diagnostics": diagnostics,
        "version": "0.1.0",
    }


__all__ = ["solve_crs"]
