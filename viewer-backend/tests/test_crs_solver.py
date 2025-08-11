from __future__ import annotations

import json

from app.crs.solver import solve_crs


def top1_epsg(res):
    return res["candidates"][0]["epsg"] if res.get("candidates") else None


def test_crs_clear_wgs84_utm_32n():
    lines = ["UTM 32N WGS84"]
    res = solve_crs(lines)
    assert top1_epsg(res) == 32632
    assert res["candidates"][0]["p"] > 0.7
    reasons = " ".join(res["candidates"][0]["reasons"]).upper()
    assert "UTM" in reasons and "ZONE 32" in reasons and "WGS84" in reasons


def test_crs_southern_hemi():
    lines = ["UTM ZONE 22S, WGS84"]
    res = solve_crs(lines)
    assert top1_epsg(res) == 32722


def test_crs_ed50_europe():
    lines = ["ED50 UTM 32"]
    res = solve_crs(lines)
    epsgs = [c["epsg"] for c in res["candidates"][:2]]
    assert 23032 in epsgs


def test_crs_nad83_utm():
    lines = ["NAD83 UTM 12N"]
    res = solve_crs(lines)
    assert top1_epsg(res) == 26912


def test_crs_ambiguous_no_datum():
    lines = ["UTM 32, METERS", "NORTH SEA"]
    res = solve_crs(lines)
    epsgs = [c["epsg"] for c in res["candidates"][:3]]
    assert 32632 in epsgs and 25832 in epsgs and 23032 in epsgs
    assert res["diagnostics"]["penalties"]


def test_crs_vintage_prior_nad27():
    lines = ["GULF OF MEXICO SURVEY", "ACQUIRED 1972", "UTM 15"]
    res = solve_crs(lines)
    # NAD27 zone 15 is 26715; should be among top due to vintage + NA region
    epsgs = [c["epsg"] for c in res["candidates"][:5]]
    assert 26715 in epsgs
