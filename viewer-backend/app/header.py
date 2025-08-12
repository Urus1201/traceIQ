from fastapi import APIRouter, UploadFile, File, Form, Query, Depends, Request
from fastapi import HTTPException
import logging
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


import hashlib


def _hash_lines(lines):
    h = hashlib.sha1()
    for ln in lines:
        h.update((ln or "").encode("utf-8", errors="ignore"))
        h.update(b"\n")
    return h.hexdigest()


@router.post("/header/iq", response_model=HeaderJSON)
async def header_iq(
    request: Request,
    file: UploadFile = File(None),
    path: str = Form(None),
) -> HeaderJSON:
    """Parse textual SEG-Y header (textual 3200 bytes -> 40x80 lines) into structured HeaderJSON.

    Accepts either an uploaded file (multipart) or a filesystem path provided as form field 'path'.
    Returns a HeaderJSON (possibly empty if nothing recognized).
    Raises explicit HTTP errors for common failure modes instead of generic 500.
    """
    tmp_path: Optional[str] = None
    try:
        # Guard against both or neither inputs
        if file and path:
            raise HTTPException(status_code=400, detail="Provide either 'file' or 'path', not both")
        if not file and not path:
            raise HTTPException(status_code=400, detail="No file or path provided")

        if file:
            # Persist upload to a temp file for existing read_text_header API
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                content = await file.read()
                if not content:
                    raise HTTPException(status_code=400, detail="Uploaded file is empty")
                tmp.write(content)
                tmp_path = tmp.name
            try:
                hdr = read_text_header(tmp_path)
            except FileNotFoundError:
                raise HTTPException(status_code=500, detail="Temporary file disappeared before reading")
            except ValueError as ve:
                # Likely not a valid SEG-Y (short header)
                raise HTTPException(status_code=422, detail=f"Invalid SEG-Y textual header: {ve}")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to read uploaded header: {e}")
        else:  # path case
            if not os.path.exists(path):
                raise HTTPException(status_code=404, detail=f"Path not found: {path}")
            if not os.path.isfile(path):
                raise HTTPException(status_code=400, detail=f"Not a regular file: {path}")
            try:
                hdr = read_text_header(path)
            except PermissionError:
                raise HTTPException(status_code=403, detail=f"Permission denied: {path}")
            except ValueError as ve:
                raise HTTPException(status_code=422, detail=f"Invalid SEG-Y textual header: {ve}")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to read textual header: {e}")

        lines = hdr.get("lines") if isinstance(hdr, dict) else None
        if not isinstance(lines, list):
            raise HTTPException(status_code=500, detail="read_text_header returned unexpected structure (missing 'lines')")
        if len(lines) != 40:
            # Continue but lower confidence; still useful for partial headers. Treat as 422 so caller sees issue.
            raise HTTPException(status_code=422, detail=f"Expected 40 header lines, got {len(lines)}")

        # Optional caching
        cache = getattr(request.app.state, "cache", None)
        cache_key = None
        if cache:
            cache_key = f"iq:{_hash_lines(lines)}"
            try:
                cached = await cache.get_json(cache_key)
            except Exception:
                cached = None
            if cached:
                try:
                    return HeaderJSON(**cached)
                except Exception:
                    # Corrupt cache entry: ignore
                    pass

        try:
            parsed = parse_header_iq(lines)
        except HTTPException:
            raise
        except Exception as e:
            logging.exception("parse_header_iq crashed")
            raise HTTPException(status_code=500, detail=f"Parser failure: {type(e).__name__}: {e}")

        if cache and cache_key:
            try:
                await cache.set_json(cache_key, parsed.model_dump())  # type: ignore[attr-defined]
            except Exception:
                pass
        return parsed
    except HTTPException:
        raise
    except Exception as e:  # broad catch to avoid opaque 500s
        import traceback
        tb = traceback.format_exc(limit=6)
        logging.error("/header/iq fatal error: %s", e)
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"error": str(e), "type": type(e).__name__, "trace": tb})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


@router.post("/header/read_binary")
async def read_binary(request: Request, file: UploadFile = File(None), path: str = Form(None)):
    """Read minimal binary header fields using segyio if available.
    Accepts either an uploaded file or a filesystem path.
    Returns: { sample_interval_us, samples_per_trace, format_code }
    """
    tmp_path = None
    try:
        if not file and not path:
            # Attempt to parse JSON body for {"path": "..."}
            try:
                payload = await request.json()
                if isinstance(payload, dict):
                    path = payload.get("path")  # type: ignore
            except Exception:
                pass
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
async def parse_header(req: ParseRequest, request: Request, provider: LLMProvider = Depends(get_llm_provider)) -> ParseResponse:
    """Phase-3 parser: combine baseline regex with optional LLM fallback and return provenance.

    Body schema:
      {
        "lines": ["C01 ...", ..., "C40 ..."],
        "use_llm": true
      }
    """
    lines = req.lines  # already normalized to exactly 40 via validator

    cache = getattr(request.app.state, "cache", None)
    cache_key = None
    if cache:
        cache_key = f"parse:{int(req.use_llm)}:{_hash_lines(lines)}"
        cached = await cache.get_json(cache_key)
        if cached:
            try:
                header_payload = cached.get("header") if isinstance(cached, dict) else None
                prov_payload = cached.get("provenance") if isinstance(cached, dict) else None
                if header_payload is not None:
                    hj = HeaderJSON(**header_payload)
                    provenance = [ProvenanceEntry(**p) for p in (prov_payload or [])]
                    return ParseResponse(header=hj, provenance=provenance)
            except Exception:
                pass

    # 1) Baseline
    base = parse_baseline(lines)
    # 2) LLM fallback (pluggable)
    llm_fields = run_llm(lines, provider) if req.use_llm and provider else {}
    # 3) Merge
    merged_fields, prov = merge_with_confidence(base, llm_fields)
    # 4) Project into HeaderJSON (filter to schema fields)
    hj = HeaderJSON(**{k: fe for k, fe in merged_fields.items() if k in HeaderJSON.model_fields})
    provenance = [ProvenanceEntry(**p) for p in prov]
    resp = ParseResponse(header=hj, provenance=provenance)
    if cache and cache_key:
        try:
            await cache.set_json(cache_key, resp.model_dump())  # type: ignore[attr-defined]
        except Exception:
            pass
    return resp


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
