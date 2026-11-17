import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import init_db
from app.routers.api import router as api_router
from app.routers.whatsapp import router as whatsapp_router
from app.security import csrf_token, needs_legacy_proof, registration_open, security_middleware
from app.security import router as auth_router
from app.services.whatsapp_scheduler import run_whatsapp_scheduler

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    scheduler_task = (
        asyncio.create_task(run_whatsapp_scheduler()) if settings.whatsapp_enabled else None
    )
    try:
        yield
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title=settings.app_name,
    description="Plataforma local-first de aprendizagem com RAG, agente de estudos, IA multi-provedor e assistente de estudos via WhatsApp/Evolution API.",
    version="0.8.0",
    lifespan=lifespan,
)

app.middleware("http")(security_middleware)


@app.get("/api/health/live", include_in_schema=False)
def liveness():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
app.include_router(auth_router)
app.include_router(api_router)
app.include_router(whatsapp_router)


@app.get("/", include_in_schema=False)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"app_name": settings.app_name, "csrf_token": csrf_token(request)},
    )


@app.get("/api")
def api_root():
    return {
        "name": settings.app_name,
        "docs": "/docs",
        "health": "/api/health",
    }


@app.get("/login", include_in_schema=False)
def login_screen(request: Request):
    if settings.openstudy_auth_mode == "off":
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request, name="login.html",
        context={"app_name": settings.app_name,
                 "registration_open": registration_open(),
                 "legacy_proof_required": needs_legacy_proof()},
    )
