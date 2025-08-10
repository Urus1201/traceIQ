# viewer-backend/tests/test_baseline_parser.py
import pytest

from extract.baseline_parser import parse_baseline

def pad40(lines):
    """Ensure we always pass exactly 40 lines to the parser."""
    return lines + [""] * (40 - len(lines))

def test_microseconds_normalization_and_derived_record_length():
    lines = pad40([
        "C  6 SAMPLE INTERVAL 4000           SAMPLES/TRACE 2500                 BITS/IN          BYTES/SAMPLE",
    ])
    out = parse_baseline(lines)

    assert "sample_interval_ms" in out
    assert out["sample_interval_ms"].value == pytest.approx(4.0, rel=0, abs=1e-9)
    assert "samples_per_trace" in out
    assert out["samples_per_trace"].value == 2500

    # derived: record_length_ms = 2500 * 4.0 = 10000 ms
    assert "record_length_ms" in out
    assert out["record_length_ms"].value == pytest.approx(10000.0, rel=0, abs=1e-9)

    # sanity: line_refs should reference the same line for both fields here
    assert 1 in out["sample_interval_ms"].line_refs
    assert 1 in out["samples_per_trace"].line_refs
    assert set(out["record_length_ms"].line_refs) == {1}

def test_explicit_record_length_overrides_derived():
    lines = pad40([
        "C  6 SAMPLE INTERVAL 4              SAMPLES/TRACE 3000",
        "C  7 RECORD LENGTH 12000 MS",
    ])
    out = parse_baseline(lines)

    assert out["sample_interval_ms"].value == pytest.approx(4.0)
    assert out["samples_per_trace"].value == 3000

    # explicit record length should be taken directly
    assert out["record_length_ms"].value == pytest.approx(12000.0)
    # and should point to line 2 (1-based)
    assert 2 in out["record_length_ms"].line_refs

def test_recording_format_and_measurement_system():
    lines = pad40([
        "C  7 Recording Format  segy",
        "C  8 MEASUREMENT SYSTEM METRIC",
    ])
    out = parse_baseline(lines)

    assert out["recording_format"].value == "SEGY"
    assert "measurement_system" in out
    assert out["measurement_system"].value == "METRIC"

def test_data_and_aux_traces_per_record():
    lines = pad40([
        "C  5 DATA TRACES/RECORD 240         AUXILIARY TRACES/RECORD 4          CDP FOLD",
    ])
    out = parse_baseline(lines)

    assert out["data_traces_per_record"].value == 240
    assert out["auxiliary_traces_per_record"].value == 4
    assert 1 in out["data_traces_per_record"].line_refs
    assert 1 in out["auxiliary_traces_per_record"].line_refs

def test_free_text_fields_company_client_area():
    lines = pad40([
        "C  1 COMPANY OVATION DATA SERVICE            CREW NO",
        "C  2 CLIENT ACME ENERGY                      MAP ID",
        "C  3 AREA GULF OF MEXICO",
    ])
    out = parse_baseline(lines)

    assert out["company"].value == "OVATION DATA SERVICE"
    assert out["client"].value == "ACME ENERGY"
    assert out["area"].value == "GULF OF MEXICO"
    assert out["company"].line_refs == [1]
    assert out["client"].line_refs == [2]
    assert out["area"].line_refs == [3]