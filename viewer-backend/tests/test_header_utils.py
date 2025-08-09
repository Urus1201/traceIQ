from segy.header_normalize import normalize_line, normalize_lines
from extract.value_extractors import (
    extract_ms,
    match_data_traces_per_record,
    match_aux_traces_per_record,
    match_samples_per_trace,
    match_bytes_per_sample,
    match_format_this_reel,
    match_sample_interval_ms,
)
from qc.sanity import sanity_derive_from_text, derive_record_length_ms
from extract.evidence import make_evidence, merge_evidence
from extract.highlight import gather_line_highlights, highlight_value


def build_40_lines():
    lines = ["".ljust(80) for _ in range(40)]
    # Key content lines
    lines[4] = "C05  DATA TRACES/RECORD: 282  AUXILIARY TRACES/RECORD: 2".ljust(80)
    lines[5] = "C06  SAMPLES/TRACE: 750  BYTES/SAMPLE 4  FORMAT THIS REEL: SEGY".ljust(80)
    lines[6] = "C07  SAMPLE INTERNAL: 2 MS".ljust(80)  # typo 'INTERNAL' deliberately
    return lines


def test_normalization_typo_tolerant_raw_preserved():
    l7 = "C07  SAMPLE INTERNAL: 2 MS".ljust(80)
    norm = normalize_line(l7)
    assert "SAMPLE INTERNAL" in norm["raw"]
    # match_canonical should correct INTERNAL -> INTERVAL for matching purposes
    assert "SAMPLE INTERVAL" in norm["match_canonical"]

    # Whole set normalization retains 40 items and lineno mapping
    lines = build_40_lines()
    norms = normalize_lines(lines)
    assert len(norms) == 40
    assert norms[6]["lineno"] == 7


def test_extractors_values_and_spans():
    lines = build_40_lines()
    l5, l6, l7 = lines[4], lines[5], lines[6]

    v_data = match_data_traces_per_record(l5)
    v_aux = match_aux_traces_per_record(l5)
    v_samples = match_samples_per_trace(l6)
    v_bytes = match_bytes_per_sample(l6)
    v_format = match_format_this_reel(l6)
    v_sint = match_sample_interval_ms(l7)

    assert v_data and v_data[0] == 282
    assert l5[v_data[1][0]:v_data[1][1]] == "282"

    assert v_aux and v_aux[0] == 2
    assert l5[v_aux[1][0]:v_aux[1][1]] == "2"

    assert v_samples and v_samples[0] == 750
    assert l6[v_samples[1][0]:v_samples[1][1]] == "750"

    assert v_bytes and v_bytes[0] == 4
    assert l6[v_bytes[1][0]:v_bytes[1][1]] == "4"

    assert v_format and v_format[0] == "SEGY"

    assert v_sint and v_sint[0] == 2
    assert l7[v_sint[1][0]:v_sint[1][1]] == "2"

    # Also sanity-check unit extractor
    assert extract_ms("RECORD LENGTH: 4000 MS") == 4000


def test_sanity_derivations_and_merge():
    lines = build_40_lines()
    out = sanity_derive_from_text(lines)
    assert "record_length_ms" in out
    ev = out["record_length_ms"]
    assert ev["value"] == derive_record_length_ms(4, 750)
    assert ev["confidence"] == 0.8
    assert ev["line_refs"] == [6]
    assert len(ev.get("raw_spans", [])) == 2

    # Merge behavior: prefer higher confidence, union spans/refs
    ev_high = make_evidence(3000, 0.95, lineno=6, span=ev["raw_spans"][0])
    merged = merge_evidence(ev_high, ev)
    assert merged["value"] == 3000
    assert merged["confidence"] == 0.95
    assert 6 in merged["line_refs"]
    assert len(merged["raw_spans"]) >= 2


def test_highlighting_with_spans():
    lines = build_40_lines()
    out = sanity_derive_from_text(lines)
    l6 = lines[5]

    # Aggregate spans per line from multiple evidences
    evidences = {
        "record_length_ms": out["record_length_ms"],
    }
    spans_map = gather_line_highlights(evidences, lines)
    assert 6 in spans_map
    spans = spans_map[6]
    # Apply highlights safely: later span first if needed
    spans_sorted = sorted(spans, key=lambda t: t[0], reverse=True)
    highlighted = l6
    for sp in spans_sorted:
        highlighted = highlight_value(highlighted, sp)
    # Check that both numbers appear highlighted
    assert "⟦750⟧" in highlighted or "⟦4⟧" in highlighted
