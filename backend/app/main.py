"""ANVITRACE — FastAPI Application Entry Point."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import settings
from app.database.duckdb import close, connect
from app.routers.cases import cases_router
from app.routers.dataset import router as dataset_router
from app.routers.evidence import documents_router, evidence_router
from app.routers.investigations import account_router, investigation_router
from app.routers.recovery import router as recovery_router
from app.explainer.ollama_client import OllamaClient
from app.routers.syndicate import router as syndicate_router
from app.utils.logging import logger

model_health_state: dict[str, Any] = {
    "model_ready": False,
    "model_hash_ok": True,
    "mode": "template_only",
    "ollama_available": False,
    "warmed_up": False,
}


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Application lifespan: startup and shutdown hooks."""
    logger.info("Starting up ANVITRACE backend platform...")
    connect()
    logger.info("DuckDB connected and canonical schema applied.")

    # Model hash verification and warm-up
    try:
        ollama = OllamaClient()
        hash_ok, reason = ollama.verify_model_integrity()
        ollama_avail = ollama.is_available() if hash_ok else False
        warmed = ollama.warmup() if (hash_ok and ollama_avail) else False

        model_health_state["model_ready"] = bool(hash_ok and ollama_avail and warmed)
        model_health_state["model_hash_ok"] = bool(hash_ok)
        model_health_state["mode"] = "model" if (hash_ok and ollama_avail and warmed) else "template_only"
        model_health_state["ollama_available"] = bool(ollama_avail)
        model_health_state["warmed_up"] = bool(warmed)

        logger.info(
            f"[main] Model startup status: mode={model_health_state['mode']} | "
            f"model_ready={model_health_state['model_ready']} | "
            f"model_hash_ok={model_health_state['model_hash_ok']} | "
            f"ollama_available={model_health_state['ollama_available']} | "
            f"warmed_up={model_health_state['warmed_up']}"
        )
    except Exception as exc:
        logger.warning(f"[main] Model integrity/warmup check failed: {exc}")
        model_health_state["model_ready"] = False
        model_health_state["model_hash_ok"] = False
        model_health_state["mode"] = "template_only"
        model_health_state["ollama_available"] = False
        model_health_state["warmed_up"] = False

    yield
    logger.info("Shutting down ANVITRACE backend platform...")
    close()
    logger.info("DuckDB connection closed safely.")


app = FastAPI(
    title=settings.APP_NAME,
    description="Offline cyber-forensics platform for financial transaction analysis and money-mule investigation.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for local forensic UI / inspection tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
@app.get("/api/health", tags=["system"])
async def health_check():
    """System health check endpoint with model readiness and mode."""
    return {
        "status": "ok",
        "project": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "database": "DuckDB (Embedded)",
        "offline": True,
        "model_ready": model_health_state.get("model_ready", False),
        "model_hash_ok": model_health_state.get("model_hash_ok", True),
        "mode": model_health_state.get("mode", "template_only"),
        "ollama_available": model_health_state.get("ollama_available", False),
        "warmed_up": model_health_state.get("warmed_up", False),
    }



# Register functional routers
app.include_router(dataset_router, prefix="/api")
app.include_router(account_router, prefix="/api")
app.include_router(investigation_router, prefix="/api")
app.include_router(recovery_router, prefix="/api")
app.include_router(syndicate_router, prefix="/api")
app.include_router(evidence_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(cases_router, prefix="/api")

# Serve offline compiled frontend
frontend_dist = settings.frontend_dist_path
if frontend_dist.exists() and (frontend_dist / "index.html").exists():
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    logger.info(f"Mounting offline frontend static assets from: {frontend_dist}")
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    @app.exception_handler(404)
    async def spa_404_handler(request, exc):
        # Allow API 404s to return standard JSON 404
        if request.url.path.startswith("/api") or request.url.path in ("/docs", "/redoc", "/openapi.json"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        # Otherwise serve index.html for SPA client-side routing
        index_file = frontend_dist / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return JSONResponse(status_code=404, content={"detail": "Not Found"})


