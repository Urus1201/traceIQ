#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from glob import glob
from typing import Dict, List, Tuple

# Make viewer-backend importable
THIS_DIR = os.path.dirname(__file__)
BACKEND_DIR = os.path.abspath(os.path.join(THIS_DIR, "..", "viewer-backend"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.schemas import FieldEvidence  # type: ignore
from extract.baseline_parser import parse_baseline  # type: ignore
from extract.llm_fallback import run_llm, merge_with_confidence, _equalish  # type: ignore
from extract.providers import build_provider_from_env  # type: ignore
from segy.header_io import read_text_header  # type: ignore
from segy.binary_header import read_binary_header  # type: ignore


def _fmt_fe(fe: FieldEvidence | None) -> str:
    if not fe:
        return "-"
    return f"{fe.value!r}@{fe.confidence:.2f} L{fe.line_refs}"


def _collect_files(data_dir: str, pattern: str, limit: int | None) -> List[str]:
    pats = [p.strip() for p in pattern.split(",") if p.strip()]
    files: List[str] = []
    for p in pats:
        files.extend(glob(os.path.join(data_dir, p)))
    files = sorted(set(files))
    if limit is not None:
        files = files[:limit]
    return files


def _compare_against_binary(path: str, baseline: Dict[str, FieldEvidence], llm: Dict[str, FieldEvidence], merged: Dict[str, FieldEvidence]) -> Dict[str, dict]:
    """Compare numeric fields to binary header as a weak ground truth.

    Returns per-source correctness for sample_interval_ms, samples_per_trace, record_length_ms.
    """
    stub = read_binary_header(path)
    exp_si = (stub.sample_interval_us / 1000.0) if stub.sample_interval_us else None
    exp_spt = stub.samples_per_trace
    exp_rl = (exp_si * exp_spt) if (exp_si and exp_spt) else None

    def ok(v, exp, tol_abs=0.5, tol_rel=0.02):
        if v is None or exp is None:
            return None
        try:
            a, b = float(v), float(exp)
            return abs(a - b) <= max(tol_abs, tol_rel * max(abs(a), abs(b)))
        except Exception:
            return False

    def pick(d: Dict[str, FieldEvidence], k: str):
        fe = d.get(k)
        return fe.value if fe else None

    return {
        "sample_interval_ms": {
            "baseline": ok(pick(baseline, "sample_interval_ms"), exp_si),
            "llm": ok(pick(llm, "sample_interval_ms"), exp_si),
            "merged": ok(pick(merged, "sample_interval_ms"), exp_si),
            "expected": exp_si,
        },
        "samples_per_trace": {
            "baseline": ok(pick(baseline, "samples_per_trace"), exp_spt, tol_abs=0, tol_rel=0),
            "llm": ok(pick(llm, "samples_per_trace"), exp_spt, tol_abs=0, tol_rel=0),
            "merged": ok(pick(merged, "samples_per_trace"), exp_spt, tol_abs=0, tol_rel=0),
            "expected": exp_spt,
        },
        "record_length_ms": {
            "baseline": ok(pick(baseline, "record_length_ms"), exp_rl),
            "llm": ok(pick(llm, "record_length_ms"), exp_rl),
            "merged": ok(pick(merged, "record_length_ms"), exp_rl),
            "expected": exp_rl,
        },
    }


def evaluate_files(files: List[str], fmt: str = "pretty", show_agree: bool = False) -> Tuple[List[dict], dict]:
    provider = build_provider_from_env()
    results: List[dict] = []
    agg = {
        "files": 0,
        "baseline_fields": 0,
        "llm_fields": 0,
        "merged_fields": 0,
        "llm_added": 0,
        "llm_overrode": 0,
        "agree": 0,
    }

    for path in files:
        hdr = read_text_header(path)
        lines = hdr["lines"]
        baseline = parse_baseline(lines)
        llm = run_llm(lines, provider) if provider else {}
        merged, provenance = merge_with_confidence(baseline, llm)

        keys = sorted(set(baseline.keys()) | set(llm.keys()))
        rows = []
        for k in keys:
            b = baseline.get(k)
            l = llm.get(k)
            status = ""  # baseline_only | llm_only | agree | disagree
            if b and not l:
                status = "baseline_only"
            elif l and not b:
                status = "llm_only"
            elif b and l:
                status = "agree" if _equalish(b.value, l.value) else "disagree"
            else:
                continue
            if status == "agree" and not show_agree:
                pass
            rows.append({
                "field": k,
                "baseline": _fmt_fe(b),
                "llm": _fmt_fe(l),
                "status": status,
            })

        comp = _compare_against_binary(path, baseline, llm, merged)

        # aggregate stats
        agg["files"] += 1
        agg["baseline_fields"] += len(baseline)
        agg["llm_fields"] += len(llm)
        agg["merged_fields"] += len(merged)
        agg["agree"] += sum(1 for r in rows if r["status"] == "agree")
        agg["llm_added"] += sum(1 for r in rows if r["status"] == "llm_only")
        agg["llm_overrode"] += sum(1 for r in rows if r["status"] == "disagree")

        results.append({
            "file": path,
            "encoding": hdr["encoding"],
            "rows": rows,
            "provenance": provenance,
            "binary_cmp": comp,
        })

    if fmt == "pretty":
        for r in results:
            print(f"\n=== {os.path.basename(r['file'])} (encoding={r['encoding']}) ===")
            for row in r["rows"]:
                print(f"- {row['field']}: baseline={row['baseline']} | llm={row['llm']} | {row['status']}")
            bc = r["binary_cmp"]
            print("  Against binary header (if available):")
            for k, d in bc.items():
                exp = d["expected"]
                print(f"    {k}: expected={exp} baseline={d['baseline']} llm={d['llm']} merged={d['merged']}")
        print("\n--- aggregate ---")
        print(json.dumps(agg, indent=2))
    return results, agg


def main():
    ap = argparse.ArgumentParser(description="Evaluate baseline vs LLM SEG-Y header parsing without touching production code.")
    ap.add_argument("--data-dir", default=os.path.join(os.getcwd(), "data"), help="Folder containing SEG-Y files")
    ap.add_argument("--pattern", default="*.sgy,*.segy,*.SGY,*.SEGY", help="Glob(s) for files, comma-separated")
    ap.add_argument("--limit", type=int, default=5, help="Max number of files")
    ap.add_argument("--format", choices=["pretty", "json", "csv"], default="pretty")
    ap.add_argument("--output", help="Optional path to write JSON/CSV output")
    ap.add_argument("--show-agree", action="store_true", help="Include fields where baseline and LLM agree")
    args = ap.parse_args()

    files = _collect_files(args.data_dir, args.pattern, args.limit)
    if not files:
        print("No files matched. Adjust --data-dir/--pattern.")
        sys.exit(1)

    results, agg = evaluate_files(files, fmt="pretty" if args.format == "pretty" else "json", show_agree=args.show_agree)

    if args.output:
        if args.format == "json":
            payload = {"aggregate": agg, "results": results}
            with open(args.output, "w") as f:
                json.dump(payload, f, indent=2)
            print(f"Wrote JSON to {args.output}")
        elif args.format == "csv":
            with open(args.output, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["file", "field", "baseline", "llm", "status"]) 
                for r in results:
                    for row in r["rows"]:
                        w.writerow([r["file"], row["field"], row["baseline"], row["llm"], row["status"]])
            print(f"Wrote CSV to {args.output}")


if __name__ == "__main__":
    main()
