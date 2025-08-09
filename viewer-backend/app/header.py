from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import JSONResponse
from segy.header_io import read_text_header
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
