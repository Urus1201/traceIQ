from fastapi import APIRouter, UploadFile, File, Form, Query
from fastapi.responses import JSONResponse
from segy.header_io import read_text_header
from app.schemas import HeaderJSON
from app.iq_parser import parse_header_iq
from segy.binary_header import read_binary_header
from qc.sanity import sanity_derive_from_text
from extract.value_extractors import (
    match_samples_per_trace,
    match_bytes_per_sample,
    match_data_traces_per_record,
    match_aux_traces_per_record,
)
import tempfile
import os

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
async def read_binary(path: str = Form(...)):
    """Read minimal binary header fields using segyio if available.
    Body: { path: "..." }
    Returns: { sample_interval_us, samples_per_trace, format_code }
    """
    stub = read_binary_header(path)
    return {
        "sample_interval_us": stub.sample_interval_us,
        "samples_per_trace": stub.samples_per_trace,
        "format_code": stub.format_code,
    }


@router.get("/header/preview_text")
async def preview_text(path: str = Query(...)):
    """Lightweight preview of textual header for manual testing.
    Returns encoding and 40x80 lines.
    """
    hdr = read_text_header(path)
    return {"encoding": hdr["encoding"], "lines": hdr["lines"]}


@router.post("/header/sanity")
async def header_sanity(path: str = Form(...)):
    """Return a small set of derived fields as FieldEvidence-like dicts.
    No full parsing; proves the utility functions end-to-end.
    Expected fields (when present):
    - sample_interval_ms (from L6 BYTES/SAMPLE 4)
    - samples_per_trace (from L6 SAMPLES/TRACE 750)
    - record_length_ms (product 4 * 750 = 3000) with both spans
    - data_traces_per_record (from L5)
    - aux_traces_per_record (from L5)
    """
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
