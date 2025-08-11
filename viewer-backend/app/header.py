from fastapi import APIRouter, UploadFile, File, Form, Query, Depends, Request
from fastapi.responses import JSONResponse
from segy.header_io import read_text_header
from app.schemas import HeaderJSON, ParseResponse, ProvenanceEntry
from app.iq_parser import parse_header_iq
from extract.baseline_parser import parse_baseline
from extract.llm_fallback import run_llm, merge_with_confidence, LLMProvider
from segy.binary_header import read_binary_header
from qc.sanity import sanity_derive_from_text
from qc.consistency import check_text_vs_binary
from extract.value_extractors import (
    match_samples_per_trace,
    match_bytes_per_sample,
    match_data_traces_per_record,
    match_aux_traces_per_record,
)
import tempfile
import os
import json
from typing import Optional, List
from fastapi import Body
from pydantic import BaseModel, field_validator
from typing import Any, Dict
from app.crs.solver import solve_crs

router = APIRouter()

@router.post("/header/read")
async def read_header(file: UploadFile = File(None), path: str = Form(None)):
    if file:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name
        try:
            result = read_text_header(tmp_path)
        finally:
            os.unlink(tmp_path)
    elif path:
        result = read_text_header(path)
    else:
        return JSONResponse(status_code=400, content={"error": "No file or path provided"})
    return {"encoding": result["encoding"], "lines": result["lines"]}


@router.post("/header/iq", response_model=HeaderJSON)
async def header_iq(file: UploadFile = File(None), path: str = Form(None)) -> HeaderJSON:
    """Parse textual header into structured HeaderJSON.
    Accepts either an uploaded file or a filesystem path.
    """
    tmp_path = None
    try:
        if file:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                content = await file.read()
                tmp.write(content)
                tmp_path = tmp.name
            hdr = read_text_header(tmp_path)
        elif path:
            hdr = read_text_header(path)
        else:
            return HeaderJSON()

        lines = hdr["lines"]
        return parse_header_iq(lines)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.post("/header/read_binary")
async def read_binary(file: UploadFile = File(None), path: str = Form(None)):
    """Read minimal binary header fields using segyio if available.
    Accepts either an uploaded file or a filesystem path.
    Returns: { sample_interval_us, samples_per_trace, format_code }
    """
    tmp_path = None
    try:
        if file:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                content = await file.read()
                tmp.write(content)
                tmp_path = tmp.name
            stub = read_binary_header(tmp_path)
        elif path:
            stub = read_binary_header(path)
        else:
            return JSONResponse(status_code=400, content={"error": "No file or path provided"})
        return {
            "sample_interval_us": stub.sample_interval_us,
            "samples_per_trace": stub.samples_per_trace,
            "format_code": stub.format_code,
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.get("/header/preview_text")
async def preview_text(path: str = Query(...)):
    """Lightweight preview of textual header for manual testing.
    Returns encoding and 40x80 lines.
    """
    hdr = read_text_header(path)
    return {"encoding": hdr["encoding"], "lines": hdr["lines"]}


@router.post("/header/sanity")
async def header_sanity(payload: dict = Body(...)):
    """Return a small set of derived fields as FieldEvidence-like dicts.
    No full parsing; proves the utility functions end-to-end.
    Expected fields (when present):
    - sample_interval_ms (from L6 BYTES/SAMPLE 4)
    - samples_per_trace (from L6 SAMPLES/TRACE 750)
    - record_length_ms (product 4 * 750 = 3000) with both spans
    - data_traces_per_record (from L5)
    - aux_traces_per_record (from L5)
    """
    path = payload.get("path")
    if not path:
        return JSONResponse(status_code=400, content={"error": "Missing 'path'"})
    hdr = read_text_header(path)
    lines = hdr["lines"]
    out = {}

    # L6-based values
    if len(lines) >= 6:
        l6 = lines[5]
        s = match_samples_per_trace(l6)
        b = match_bytes_per_sample(l6)
        if b:
            out["sample_interval_ms"] = {
                "value": b[0],
                "confidence": 0.9,
                "line_refs": [6],
                "raw_spans": [b[1]],
            }
        if s:
            out["samples_per_trace"] = {
                "value": s[0],
                "confidence": 0.9,
                "line_refs": [6],
                "raw_spans": [s[1]],
            }

    # Derived record length using QC util (expects both spans when available)
    out.update(sanity_derive_from_text(lines))

    # L5-based values
    if len(lines) >= 5:
        l5 = lines[4]
        d = match_data_traces_per_record(l5)
        a = match_aux_traces_per_record(l5)
        if d:
            out["data_traces_per_record"] = {
                "value": d[0],
                "confidence": 0.9,
                "line_refs": [5],
                "raw_spans": [d[1]],
            }
        if a:
            out["aux_traces_per_record"] = {
                "value": a[0],
                "confidence": 0.9,
                "line_refs": [5],
                "raw_spans": [a[1]],
            }

    return out


def _peek_first_trace_samples(path: str) -> Optional[int]:
    """Best-effort peek of the first trace length using segyio; returns None if unavailable.
    Keeps IO minimal by aborting after the first trace.
    """
    try:
        import segyio  # type: ignore

        with segyio.open(path, mode="r", strict=False, ignore_geometry=True) as f:  # type: ignore[attr-defined]
            try:
                tr0 = f.trace[0]
                return int(len(tr0))
            except Exception:
                return None
    except Exception:
        return None



@router.post("/header/apply_patch")
async def apply_patch(payload: dict = Body(...)):
    """Create a sidecar JSON aligning textual header fields with binary header.

    Payload: { path: str }
    Writes: <path>.header.sidecar.json with structure:
      - original_text: header sanity dictionary (subset)
      - corrected: fields aligned to binary
      - issues: list from check_text_vs_binary
      - source for corrected fields: "binary"
      - binary_peek: optional validation of samples_per_trace from first trace
    """
    try:
        path = payload.get("path")
        if not path:
            return JSONResponse(status_code=400, content={"error": "Missing 'path'"})

        # Gather text-derived sanity and binary header
        hdr = read_text_header(path)
        lines = hdr["lines"]
        text_sanity = {}

        # reuse the logic in header_sanity (L5/L6 expectations)
        if len(lines) >= 6:
            l6 = lines[5]
            s = match_samples_per_trace(l6)
            b = match_bytes_per_sample(l6)
            if b:
                text_sanity["sample_interval_ms"] = {
                    "value": b[0],
                    "confidence": 0.9,
                    "line_refs": [6],
                    "raw_spans": [b[1]],
                }
            if s:
                text_sanity["samples_per_trace"] = {
                    "value": s[0],
                    "confidence": 0.9,
                    "line_refs": [6],
                    "raw_spans": [s[1]],
                }

        text_sanity.update(sanity_derive_from_text(lines))

        bin_stub = read_binary_header(path)
        bin_dict = {
            "sample_interval_us": bin_stub.sample_interval_us,
            "samples_per_trace": bin_stub.samples_per_trace,
            "format_code": bin_stub.format_code,
        }

        # Consistency check and patch proposal
        result = check_text_vs_binary(text_sanity, bin_dict)

        corrected = {}
        for item in result.get("suggested_patch", []):
            corrected[item["field"]] = {
                "value": item["new_value"],
                "source": "binary",
                "rationale": item.get("rationale"),
            }

        # Optional binary peek validator
        peek_samples = _peek_first_trace_samples(path)
        binary_peek = {
            "first_trace_samples": peek_samples,
            "validated": (peek_samples is not None and corrected.get("samples_per_trace", {}).get("value") == peek_samples),
        }

        sidecar = {
            "original_text": text_sanity,
            "corrected": corrected,
            "issues": result.get("issues", []),
            "binary": bin_dict,
            "binary_peek": binary_peek,
        }

        sidecar_path = f"{path}.header.sidecar.json"
        note: Optional[str] = None
        try:
            with open(sidecar_path, "w", encoding="utf-8") as f:
                json.dump(sidecar, f, indent=2)
            written_path = sidecar_path
        except Exception:
            # Read-only dir (e.g., /data). Fall back to SIDECAR_DIR or /app/sidecars
            fallback_dir = os.environ.get("SIDECAR_DIR", "/app/sidecars")
            try:
                os.makedirs(fallback_dir, exist_ok=True)
                fallback_path = os.path.join(
                    fallback_dir, os.path.basename(sidecar_path)
                )
                with open(fallback_path, "w", encoding="utf-8") as f:
                    json.dump(sidecar, f, indent=2)
                written_path = fallback_path
                note = f"Primary location not writable; wrote to {fallback_dir}"
            except Exception as e2:
                return JSONResponse(
                    status_code=500,
                    content={"error": f"Failed to write sidecar: {e2}"},
                )

        resp = {"written": written_path, **sidecar}
        if note:
            resp["note"] = note
        return resp
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


class _NoopProvider:
    def infer(self, prompt: str) -> dict:
        # Default offline provider that returns empty result; tests will patch with a mock
        return {"header": {}}


class ParseRequest(BaseModel):
    lines: List[str]
    use_llm: bool = True

    @field_validator("lines")
    @classmethod
    def _normalize_lines(cls, v: List[str]) -> List[str]:
        if not isinstance(v, list) or len(v) == 0:
            raise ValueError("'lines' must be a non-empty list of strings")
        # Coerce to str, trim/pad to exactly 40 lines
        v = [str(x) for x in v[:40]]
        if len(v) < 40:
            v += [""] * (40 - len(v))
        return v

def get_llm_provider(request: Request) -> LLMProvider:
    # Allow apps/tests to inject a real provider via app.state.llm_provider
    prov = getattr(request.app.state, "llm_provider", None)
    return prov if prov is not None else _NoopProvider()


@router.post("/header/parse", response_model=ParseResponse)
async def parse_header(req: ParseRequest, provider: LLMProvider = Depends(get_llm_provider)) -> ParseResponse:
    """Phase-3 parser: combine baseline regex with optional LLM fallback and return provenance.

    Body schema:
      {
        "lines": ["C01 ...", ..., "C40 ..."],
        "use_llm": true
      }
    """
    lines = req.lines  # already normalized to exactly 40 via validator

    # 1) Baseline
    base = parse_baseline(lines)

    # 2) LLM fallback (pluggable)
    llm_fields = run_llm(lines, provider) if req.use_llm and provider else {}

    # 3) Merge
    merged_fields, prov = merge_with_confidence(base, llm_fields)

    # 4) Project into HeaderJSON (filter to schema fields)
    hj = HeaderJSON(**{k: fe for k, fe in merged_fields.items() if k in HeaderJSON.model_fields})

    provenance = [ProvenanceEntry(**p) for p in prov]
    return ParseResponse(header=hj, provenance=provenance)


class CRSSolveRequest(BaseModel):
        lines: List[str]
        bin_header: Dict[str, Any] | None = None
        trace_stats: Dict[str, Any] | None = None

        @field_validator("lines")
        @classmethod
        def _normalize_lines_crs(cls, v: List[str]) -> List[str]:
                if not isinstance(v, list) or len(v) == 0:
                        raise ValueError("'lines' must be a non-empty list of strings")
                # Keep as-is; CRS heuristics do not require exactly 40 lines but we cap to 200 for safety
                v = [str(x) for x in v[:200]]
                return v


@router.post("/header/crs_solve")
async def crs_solve(req: CRSSolveRequest):
        """Phase-4 CRS ranker: returns candidate EPSG codes with probabilities and diagnostics.

        Body schema:
            {
                "lines": ["C01 ...", ...],
                "bin_header": {"sample_interval": 1000},
                "trace_stats": {"grid_dx": 25.0, "grid_dy": 25.0, "units": "m"}
            }
        """
        result = solve_crs(req.lines, bin_header=req.bin_header, trace_stats=req.trace_stats)
        return result
