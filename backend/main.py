import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin_auth import bootstrap_admins
from app.config import get_settings
from app.repository import get_repository
from app.routes.analyze import router as analyze_router
from app.routes.chat import router as chat_router
from app.routes.daily_summary import router as daily_summary_router
from app.routes.evaluation import router as evaluation_router


settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    bootstrap_admins(get_repository(), settings)
    yield


app = FastAPI(
    title="NUTRIA API",
    description="Estimasi nutrisi makanan dari foto untuk WhatsApp.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(analyze_router)
app.include_router(chat_router)
app.include_router(daily_summary_router)
app.include_router(evaluation_router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "message": "NUTRIA backend is running"}
