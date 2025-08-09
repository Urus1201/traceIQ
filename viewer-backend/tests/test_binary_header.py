from fastapi.testclient import TestClient
from app.main import app


def test_read_binary_header_fallback(tmp_path):
    # Use a non-segy file to trigger fallback path if segyio can't read
    p = tmp_path / "not_a_segy.sgy"
    p.write_bytes(b"not a segy file")

    client = TestClient(app)
    resp = client.post("/header/read_binary", data={"path": str(p)})
    assert resp.status_code == 200
    data = resp.json()

    # When segyio is missing or unreadable, we expect Nones
    assert set(data.keys()) == {"sample_interval_us", "samples_per_trace", "format_code"}
    assert data["sample_interval_us"] is None
    assert data["samples_per_trace"] is None
    assert data["format_code"] is None
