from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import JSONResponse
from segy.header_io import read_text_header
from app.schemas import HeaderJSON, FieldEvidence
from app.iq_parser import parse_header_iq
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
