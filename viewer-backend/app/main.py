from fastapi import FastAPI
from app.header import router as header_router
from extract.providers import build_provider_from_env

def create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.include_router(header_router)

    provider = build_provider_from_env()
    if provider is not None:
        app.state.llm_provider = provider
    return app

app = create_app()
