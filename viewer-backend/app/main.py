from fastapi import FastAPI
from app.header import router as header_router
from extract.providers import build_provider_from_env
from app.cache import build_cache_from_env
import asyncio

def create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.include_router(header_router)

    provider = build_provider_from_env()
    if provider is not None:
        app.state.llm_provider = provider

    # Initialize cache synchronously
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:  # pragma: no cover
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    try:
        cache = loop.run_until_complete(build_cache_from_env())
        app.state.cache = cache
    except Exception:  # pragma: no cover
        pass
    return app

app = create_app()
