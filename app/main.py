"""Aplikasi Utama FastAPI ADMS Server ZKTeco."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.db.session import init_models, engine
from app.iclock.router import router as iclock_router
from app.api.devices import router as devices_api_router
from app.api.employees import router as employees_api_router
from app.api.attlog import router as attlog_api_router
from app.api.commands import router as commands_api_router
from app.api.stats import router as stats_api_router
from app.web.routes import router as web_router

# Setup Logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
)
logger = logging.getLogger("adms.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Siklus hidup aplikasi: inisialisasi skema DB saat startup."""
    logger.info(f"Menginisialisasi {settings.APP_NAME} v{settings.APP_VERSION}...")
    try:
        await init_models(engine)
        logger.info("Database berhasil diinisialisasi / diverifikasi.")
    except Exception as e:
        logger.error(f"Gagal inisialisasi database saat startup: {e}", exc_info=True)
    yield
    logger.info("Aplikasi ADMS dimatikan.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Automatic Data Master Server (ADMS) untuk Mesin Absensi ZKTeco (SenseFace 4A + M2-LR)",
    lifespan=lifespan,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registrasi Router
# 1. Protokol Mesin iClock
app.include_router(iclock_router)

# 2. REST API Admin
app.include_router(devices_api_router)
app.include_router(employees_api_router)
app.include_router(attlog_api_router)
app.include_router(commands_api_router)
app.include_router(stats_api_router)

# 3. Web UI Dashboard
app.include_router(web_router)


@app.get("/health", tags=["Health"])
async def health_check():
    """Endpoint status kesehatan server."""
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }
