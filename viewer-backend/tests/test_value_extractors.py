from extract.value_extractors import (
    extract_ms,
    extract_int_after,
    match_data_traces_per_record,
    match_aux_traces_per_record,
    match_samples_per_trace,
    match_bytes_per_sample,
    match_format_this_reel,
    match_sample_interval_ms,
)


def test_ms_basic():
    assert extract_ms("RECORD LENGTH: 4000 MS") == 4000
    assert extract_ms("no units") is None


def test_label_after():
    val = extract_int_after(r"BYTES/SAMPLE", "BYTES/SAMPLE 4")[0]
    assert val == 4


def test_line_5_to_7_extractors():
    # Craft canonical-like lines similar to a typical textual header L5-L7
    l5 = "C05  DATA TRACES/RECORD: 282  AUXILIARY TRACES/RECORD: 2".ljust(80)
    l6 = "C06  SAMPLES/TRACE: 750  BYTES/SAMPLE 4  FORMAT THIS REEL: SEGY".ljust(80)
    l7 = "C07  SAMPLE INTERNAL: 2 MS".ljust(80)

    v1 = match_data_traces_per_record(l5)
    v2 = match_aux_traces_per_record(l5)
    v3 = match_samples_per_trace(l6)
    v4 = match_bytes_per_sample(l6)
    v5 = match_format_this_reel(l6)
    v6 = match_sample_interval_ms(l7)

    assert v1 and v1[0] == 282
    assert v2 and v2[0] == 2
    assert v3 and v3[0] == 750
    assert v4 and v4[0] == 4
    assert v5 and v5[0] == "SEGY"
    assert v6 and v6[0] == 2

    # sanity: spans are within string bounds and correspond to the numeric field
    for val, (start, end) in [v1, v2, v3, v4, v6]:
        assert 0 <= start < end <= len(l5 if val in (282, 2) else l6 if val in (750, 4) else l7)
