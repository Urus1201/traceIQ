from extract.evidence import make_evidence, merge_evidence


def test_merge_keeps_higher_confidence_and_unions_lines():
    e1 = make_evidence(2.0, 0.6, lineno=6)
    e2 = make_evidence("2.0", 0.9, lineno=7)

    merged = merge_evidence(e1, e2)

    # higher confidence (e2)
    assert merged["confidence"] == 0.9
    assert float(merged["value"]) == 2.0
    # union of line refs
    assert merged["line_refs"] == [6, 7]


def test_merge_equal_confidence_numeric_agree_pref_kept():
    e1 = make_evidence(4000, 0.8, lineno=6)
    e2 = make_evidence(4000.0, 0.8, lineno=8)

    merged = merge_evidence(e1, e2)

    assert merged["confidence"] == 0.8
    # prefers pref's value type when numerically equal
    assert isinstance(merged["value"], int)
    assert merged["line_refs"] == [6, 8]
