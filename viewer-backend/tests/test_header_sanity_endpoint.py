from fastapi.testclient import TestClient
from app.main import app


def test_header_sanity_endpoint(tmp_path):
    # Construct minimal lines to satisfy L5 and L6 expectations
    l5 = "C05  DATA TRACES/RECORD: 282  AUXILIARY TRACES/RECORD: 2".ljust(80)
    l6 = "C06  SAMPLES/TRACE: 750  BYTES/SAMPLE 4  FORMAT THIS REEL: SEGY".ljust(80)
    # Write a fake file consisting of 3200 bytes with our lines at correct positions
    lines = ["".ljust(80) for _ in range(40)]
    lines[4] = l5
    lines[5] = l6
    content = ("".join(lines)).encode("ascii")

    p = tmp_path / "header_only.segy"
    p.write_bytes(content)

    client = TestClient(app)

    # Preview should return the same two lines
    prev = client.get("/header/preview_text", params={"path": str(p)})
    assert prev.status_code == 200
    prev_json = prev.json()
    assert prev_json["lines"][4].startswith("C05  DATA TRACES/RECORD")
    assert prev_json["lines"][5].startswith("C06  SAMPLES/TRACE")

    # Sanity endpoint should produce five fields with spans and line refs
    resp = client.post("/header/sanity", json={"path": str(p)})
    assert resp.status_code == 200
    data = resp.json()

    assert data["sample_interval_ms"]["value"] == 4
    assert data["samples_per_trace"]["value"] == 750
    assert data["record_length_ms"]["value"] == 3000
    assert data["data_traces_per_record"]["value"] == 282
    assert data["aux_traces_per_record"]["value"] == 2

    # Check spans and line refs exist
    for key in [
        "sample_interval_ms",
        "samples_per_trace",
        "record_length_ms",
        "data_traces_per_record",
        "aux_traces_per_record",
    ]:
        assert data[key]["line_refs"], f"missing line_refs for {key}"
        assert data[key]["raw_spans"], f"missing raw_spans for {key}"
