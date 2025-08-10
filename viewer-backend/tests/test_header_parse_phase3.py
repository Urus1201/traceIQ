from fastapi.testclient import TestClient
from app.main import app
from tests.test_header_io import make_cp037_header

from extract.llm_fallback import LLMProvider

client = TestClient(app)


class MockProvider(LLMProvider):
    def __init__(self, payload):
        self.payload = payload
    def infer(self, prompt: str) -> dict:
        return self.payload


def make_lines_with(values):
    raw, lines = make_cp037_header()
    lines_mod = list(lines)
    # helper to inject tokens
    if 'sample_interval_ms' in values:
        lines_mod[6-1] = lines_mod[6-1][:5] + f"SAMPLE INTERVAL: {values['sample_interval_ms']} MS  SAMPLES/TRACE {values.get('samples_per_trace', 750)}  BYTES/SAMPLE {values.get('bytes_per_sample', 4)}".ljust(75)
    if 'record_length_ms' in values and 'sample_interval_ms' not in values:
        lines_mod[6-1] = lines_mod[6-1][:5] + f"RECORD LENGTH: {values['record_length_ms']} MS".ljust(75)
    # Support case where only samples_per_trace is provided
    if 'samples_per_trace' in values and 'sample_interval_ms' not in values and 'record_length_ms' not in values:
        s = values['samples_per_trace']
        bps = values.get('bytes_per_sample', 4)
        lines_mod[6-1] = lines_mod[6-1][:5] + f"SAMPLES/TRACE {s}  BYTES/SAMPLE {bps}".ljust(75)
    if 'company' in values:
        lines_mod[1-1] = lines_mod[1-1][:5] + f"COMPANY: {values['company']}".ljust(75)
    if 'area' in values:
        lines_mod[2-1] = lines_mod[2-1][:5] + f"AREA: {values['area']}".ljust(75)
    return lines_mod


def post_parse(lines, use_llm=True, llm_payload=None):
    # Inject a pluggable provider into the FastAPI app state for this request (DI now available)
    if llm_payload is not None:
        app.state.llm_provider = MockProvider(llm_payload)
    else:
        # Remove any previous provider so fallback/noop is used
        if hasattr(app.state, "llm_provider"):
            delattr(app.state, "llm_provider")
    body = {"lines": lines, "use_llm": use_llm}
    return client.post('/header/parse', json=body)


def test_phase3_acceptance_numeric_and_text_fields():
    # Case 1: explicit numeric on L6, company/area present
    lines = make_lines_with({
        'sample_interval_ms': 4,
        'samples_per_trace': 4096,
        'bytes_per_sample': 4,
        'company': 'OVATION DATA SERVICE',
        'area': 'GULF OF MEXICO',
    })
    resp = post_parse(lines)
    assert resp.status_code == 200
    data = resp.json()
    hdr = data['header']
    assert hdr['sample_interval_ms']['value'] == 4.0
    assert hdr['samples_per_trace']['value'] == 4096
    assert hdr['record_length_ms']['value'] == 16384.0
    assert hdr['company']['value'] == 'OVATION DATA SERVICE'
    assert hdr['area']['value'] == 'GULF OF MEXICO'
    assert any(p['field']=='sample_interval_ms' for p in data['provenance'])


def test_phase3_explicit_record_length_only():
    # Case 2: only record length given explicitly
    lines = make_lines_with({ 'record_length_ms': 3000 })
    resp = post_parse(lines)
    hdr = resp.json()['header']
    assert hdr['record_length_ms']['value'] == 3000.0


def test_phase3_missing_numeric_baseline_but_text_present():
    # Case 3: no numeric hints, but area/company present; baseline picks them
    lines = make_lines_with({'company': 'ACME', 'area': 'NORTH SEA'})
    resp = post_parse(lines)
    hdr = resp.json()['header']
    assert hdr['company']['value'] == 'ACME'
    assert hdr['area']['value'] == 'NORTH SEA'


def test_phase3_samples_per_trace_present_only():
    # Case 4: samples per trace only should populate that field
    lines = make_lines_with({ 'samples_per_trace': 800, 'bytes_per_sample': 4 })
    resp = post_parse(lines)
    hdr = resp.json()['header']
    assert hdr['samples_per_trace']['value'] == 800


def test_phase3_coverage_requirement():
    # Case 5: ensure at least baseline handles typical L6 composite line
    lines = make_lines_with({ 'sample_interval_ms': 2, 'samples_per_trace': 4096, 'bytes_per_sample': 2 })
    resp = post_parse(lines)
    data = resp.json()
    hdr = data['header']
    # acceptance: >=80% exact match on sample_interval_ms & record_length_ms and some text fields across our tests.
    assert hdr['sample_interval_ms']['value'] == 2.0
    assert hdr['record_length_ms']['value'] == 8192.0
    assert isinstance(data['provenance'], list)


# LLM merge/DI tests
import pytest

def test_phase3_llm_merge_agree_confidence_boost():
    # Baseline provides SI=4.0ms; LLM agrees with slightly lower confidence
    lines = make_lines_with({'sample_interval_ms': 4, 'samples_per_trace': 1000})
    llm_payload = {
        'header': {
            'sample_interval_ms': {'value': 4.0, 'confidence': 0.80, 'line_refs': [6]},
            'samples_per_trace': {'value': 1000, 'confidence': 0.70, 'line_refs': [6]},
        }
    }
    resp = post_parse(lines, use_llm=True, llm_payload=llm_payload)
    assert resp.status_code == 200
    data = resp.json()
    hdr = data['header']
    # value preserved and confidence boosted above baseline/llm
    assert hdr['sample_interval_ms']['value'] == 4.0
    # merged_agree boosts: (0.9 + 0.8)/2 + 0.10 = 0.95 (capped to 1.0)
    assert hdr['sample_interval_ms']['confidence'] >= 0.95
    # provenance should include a merged_agree entry for this field
    assert any(p['field'] == 'sample_interval_ms' and p['source'] == 'merged_agree' for p in data['provenance'])


def test_phase3_llm_merge_disagree_prefers_higher_conf_with_penalty():
    # Baseline SI=4.0ms (conf ~0.9). LLM proposes 5.0ms with higher confidence 0.95.
    lines = make_lines_with({'sample_interval_ms': 4, 'samples_per_trace': 1000})
    llm_payload = {
        'header': {
            'sample_interval_ms': {'value': 5.0, 'confidence': 0.95, 'line_refs': [6]},
        }
    }
    resp = post_parse(lines, use_llm=True, llm_payload=llm_payload)
    assert resp.status_code == 200
    data = resp.json()
    hdr = data['header']
    # LLM should win but receive a small penalty (0.95 - 0.05 = 0.90)
    assert hdr['sample_interval_ms']['value'] == 5.0
    assert hdr['sample_interval_ms']['confidence'] == pytest.approx(0.90, rel=0, abs=1e-6)
    # provenance should reflect LLM as source
    assert any(p['field'] == 'sample_interval_ms' and p['source'] == 'llm' for p in data['provenance'])
