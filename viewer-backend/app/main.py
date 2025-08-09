
from fastapi import FastAPI
from app.header import router as header_router

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

app.include_router(header_router)
