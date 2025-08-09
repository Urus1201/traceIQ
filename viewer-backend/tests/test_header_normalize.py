from segy.header_normalize import normalize_line, normalize_lines
from tests.test_header_io import make_cp037_header


def test_typo_fix_applies_only_for_matching_not_display():
    # Craft a line with 'SAMPLE INTERNAL' typo
    raw_line = "C06  SAMPLE INTERNAL: 2.0 MS  RECORD LENGTH: 4000 MS".ljust(80)
    rec = normalize_line(raw_line)

    # Raw stays untouched
    assert rec["raw"].startswith("C06  SAMPLE INTERNAL: 2.0 MS")

    # Canonical uppercased, spaces collapsed; still shows INTERNAL (no display change)
    assert "SAMPLE INTERNAL" in rec["canonical"]

    # Matching views see the typo corrected to INTERVAL
    assert "SAMPLE INTERVAL" in rec["match_canonical"], rec

    # Tokens reflect the same behavior
    assert "INTERNAL" in rec["tokens"]
    assert "INTERVAL" in rec["match_tokens"]


def test_normalize_lines_adds_lineno_and_lengths():
    _, lines = make_cp037_header()
    norm = normalize_lines(lines)
    assert len(norm) == 40
    assert norm[0]["lineno"] == 1
    assert norm[-1]["lineno"] == 40
