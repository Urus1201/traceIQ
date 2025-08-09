from fastapi.testclient import TestClient
from app.main import app
import tempfile
from tests.test_header_io import make_cp037_header

client = TestClient(app)

def test_header_iq_parses_minimal_fields_cp037():
    raw, lines = make_cp037_header()
    # Insert a few hints into the lines to trigger parser
    l = list(lines)
    l[0] = l[0][:5] + "SURVEY: ACME_2020_NSEA".ljust(75)
    l[1] = l[1][:5] + "AREA: NORTH SEA".ljust(75)
    l[2] = l[2][:5] + "CONTRACTOR: ACME GEO".ljust(75)
    l[3] = l[3][:5] + "ACQUISITION YEAR: 2020".ljust(75)
    l[5] = l[5][:5] + "SAMPLE INTERVAL: 2.0 MS  RECORD LENGTH: 4000 MS".ljust(75)
    l[7] = l[7][:5] + "INLINE SPACING: 25 M  CROSSLINE SPACING: 25 M  BIN SIZE: 12.5 M".ljust(75)
    # Re-encode to cp037
    text = ''.join([s[:80] for s in l])
    raw2 = text.encode('cp037')

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(raw2)
        tmp.flush()
        tmp_path = tmp.name

    try:
        with open(tmp_path, 'rb') as f:
            files = {'file': ('header', f.read())}
            resp = client.post('/header/iq', files=files)
        assert resp.status_code == 200
        data = resp.json()
        assert data['survey_name']['value'] == 'ACME_2020_NSEA'
        assert data['area']['value'] == 'North Sea'
        assert data['contractor']['value'] == 'Acme Geo'
        assert data['acquisition_year']['value'] == 2020
        assert data['sample_interval_ms']['value'] == 2.0
        assert data['record_length_ms']['value'] == 4000.0
        assert data['inline_spacing_m']['value'] == 25.0
        assert data['crossline_spacing_m']['value'] == 25.0
        assert data['bin_size_m']['value'] == 12.5
    finally:
        import os
        os.unlink(tmp_path)
