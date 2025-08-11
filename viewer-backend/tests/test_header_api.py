from fastapi.testclient import TestClient
from app.main import app
import tempfile
from segy.header_io import read_text_header

client = TestClient(app)

def test_post_header_read_cp037():
    # Prepare a cp037 header
    raw, _ = b'', []
    from tests.test_header_io import make_cp037_header
    raw, expected_lines = make_cp037_header()
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(raw)
        tmp.flush()
        tmp_path = tmp.name
    try:
        with open(tmp_path, 'rb') as f:
            files = {'file': ('header', f.read())}
            resp = client.post('/header/read', files=files)
        assert resp.status_code == 200
        data = resp.json()
        assert data['encoding'] == 'cp037'
        assert data['lines'] == expected_lines
        assert len(data['lines']) == 40
    finally:
        import os
        os.unlink(tmp_path)


def test_header_crs_solve_endpoint():
    payload = {
        "lines": ["UTM 32N WGS84"],
        "trace_stats": {"units": "m"}
    }
    resp = client.post('/header/crs_solve', json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "candidates" in data and len(data["candidates"]) > 0
    top1 = data["candidates"][0]
    assert top1["epsg"] == 32632
    assert top1["p"] > 0.7
