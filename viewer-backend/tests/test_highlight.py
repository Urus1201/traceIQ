from extract.highlight import highlight_value, gather_line_highlights
from extract.value_extractors import (
    match_samples_per_trace,
    match_bytes_per_sample,
)


def test_highlight_l6_spans():
    # Construct an L6-like line
    l6 = "C06  SAMPLES/TRACE: 750  BYTES/SAMPLE 4  RECORD LENGTH: 4000 MS".ljust(80)

    s = match_samples_per_trace(l6)
    b = match_bytes_per_sample(l6)

    assert s and s[0] == 750
    assert b and b[0] == 4

    # Highlight both spans individually - apply later span first to avoid index shifts
    # '4' appears after '750' in the line, so highlight the '4' first, then '750'
    l6_b = highlight_value(l6, b[1])
    l6_sb = highlight_value(l6_b, s[1])

    # Ensure markers present
    assert "⟦750⟧" in l6_sb
    assert "⟦4⟧" in l6_sb


def test_gather_line_highlights():
    l6 = "C06  SAMPLES/TRACE: 750  BYTES/SAMPLE 4  RECORD LENGTH: 4000 MS".ljust(80)
    lines = ["".ljust(80)] * 5 + [l6] + ["".ljust(80)] * 34

    s = match_samples_per_trace(l6)
    b = match_bytes_per_sample(l6)

    evidences = {
        "samples_per_trace": {"value": 750, "confidence": 0.9, "line_refs": [6], "raw_spans": [s[1]]},
        "bytes_per_sample": {"value": 4, "confidence": 0.9, "line_refs": [6], "raw_spans": [b[1]]},
    }

    spans = gather_line_highlights(evidences, lines)
    assert 6 in spans
    # Should contain both spans
    ss = spans[6]
    assert s[1] in ss
    assert b[1] in ss
