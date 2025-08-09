from qc.sanity import derive_record_length_ms, sanity_derive_from_text


def test_derive_record_length_ms_basic():
    assert derive_record_length_ms(4, 750) == 3000


def test_sanity_derive_from_text_l6():
    # L6-like line containing both numbers
    l6 = "C06  SAMPLES/TRACE: 750  BYTES/SAMPLE 4  FORMAT THIS REEL: SEGY".ljust(80)
    lines = ["".ljust(80)] * 5 + [l6] + ["".ljust(80)] * 34

    out = sanity_derive_from_text(lines)
    assert "record_length_ms" in out
    ev = out["record_length_ms"]

    assert ev["value"] == 3000
    assert ev["confidence"] == 0.8
    assert ev["line_refs"] == [6]

    spans = ev.get("raw_spans", [])
    assert len(spans) == 2

    # Verify the actual highlighted substrings
    s1, s2 = spans
    s = l6[s1[0]:s1[1]] + " " + l6[s2[0]:s2[1]]
    assert "4" in s and "750" in s
