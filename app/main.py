import os
from contextlib import asynccontextmanager

# Đảm bảo tiến trình không bị kẹt ở thư mục /root gây lỗi PermissionError khi gọi subprocess
try:
    for cand_dir in ["/opt/presspanel", "/opt/wpc", "/tmp"]:
        if os.path.isdir(cand_dir) and os.access(cand_dir, os.R_OK):
            os.chdir(cand_dir)
            break
except Exception:
    pass

import hashlib
import sqlite3
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse

from app.core.config import settings, CONF_DATA
from app.db.init_db import init_database
from app.api.v1.auth import router as auth_router
from app.api.v1.domains import router as domains_router
from app.api.v1.wp import router as wp_router
from app.api.v1.files import router as files_router
from app.api.v1.system import router as system_router
from app.api.v1.apps import router as apps_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.users import router as users_router
from app.api.v1.terminal import router as terminal_router
from app.api.v1.databases import router as databases_router
import asyncio
from app.api.v1.crons import router as crons_router
from app.api.v1.backups import router as backups_router
from app.services.metrics_collector import start_metrics_collector
from app.services.mysql_controller import start_mysql_controller_worker

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Khởi động: Tạo cấu trúc SQLite nếu chưa có
    await init_database()
    # Khởi động tiến trình ngầm ghi nhận chỉ số tài nguyên mỗi phút vào SQLite DB
    metrics_task = asyncio.create_task(start_metrics_collector())
    # Khởi động PressPanel MySQL Resource Controller Worker (chu kỳ 10s: Telemetry, Active Sniper, Circuit Breaker)
    mysql_controller_task = asyncio.create_task(start_mysql_controller_worker())
    yield
    # Tắt dịch vụ: cleanup nếu cần
    metrics_task.cancel()
    mysql_controller_task.cancel()

_ENABLE_DOCS = os.getenv("PRESSPANEL_DEV_DOCS") == "1" or CONF_DATA.get("enable_docs", "0") in ("1", "true", "yes")

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="PressPanel: Control Panel Tối Ưu WordPress Cho Linux (FastAPI + C Gatekeepers)",
    docs_url="/docs" if _ENABLE_DOCS else None,
    redoc_url="/redoc" if _ENABLE_DOCS else None,
    openapi_url="/openapi.json" if _ENABLE_DOCS else None,
    lifespan=lifespan
)

# CORS Middleware (Siết chặt nguồn gốc, không mở wildcard toàn cục)
_ALLOWED_ORIGINS = [
    f"http://{settings.SERVERNAME}:{settings.PORT}",
    f"https://{settings.SERVERNAME}:{settings.PORT}",
    f"http://localhost:{settings.PORT}",
    f"http://127.0.0.1:{settings.PORT}",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Đăng ký các Router REST API
app.include_router(auth_router, prefix=settings.API_V1_STR)
app.include_router(domains_router, prefix=settings.API_V1_STR)
app.include_router(wp_router, prefix=settings.API_V1_STR)
app.include_router(files_router, prefix=settings.API_V1_STR)
app.include_router(system_router, prefix=settings.API_V1_STR)
app.include_router(apps_router, prefix=settings.API_V1_STR)
app.include_router(tasks_router, prefix=settings.API_V1_STR)
app.include_router(users_router, prefix=settings.API_V1_STR)
app.include_router(terminal_router, prefix=settings.API_V1_STR)
app.include_router(databases_router, prefix=settings.API_V1_STR)
app.include_router(crons_router, prefix=settings.API_V1_STR)
app.include_router(backups_router, prefix=settings.API_V1_STR)

@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok", "project": settings.PROJECT_NAME, "version": settings.VERSION}

def get_index_html_path() -> str:
    # 1. Đường dẫn triển khai trên máy chủ
    server_index = os.path.join(settings.STATIC_DIR, "index.html")
    if os.path.isfile(server_index):
        return server_index
    # 2. Đường dẫn cục bộ khi dev
    local_index = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "index.html")
    if os.path.isfile(local_index):
        return local_index
    return ""

def get_panel_secret_uri() -> str:
    try:
        if os.path.isfile(settings.DB_PATH):
            conn = sqlite3.connect(settings.DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT value FROM system_settings WHERE key = 'panel_secret_uri'")
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                return row[0].strip().lstrip("/")
    except Exception:
        pass
    return ""

def _portal_cookie_token(secret_uri: str) -> str:
    return hashlib.sha256(f"{secret_uri}:{settings.SECRET_KEY}".encode("utf-8")).hexdigest()[:24]

@app.middleware("http")
async def add_security_and_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Mount Static UI nếu thư mục static tồn tại
if os.path.exists(settings.STATIC_DIR):
    app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")

@app.get("/", include_in_schema=False)
async def serve_root(request: Request):
    secret_uri = get_panel_secret_uri()
    if secret_uri:
        cookie_val = request.cookies.get("pp_portal_ok", "")
        if cookie_val != _portal_cookie_token(secret_uri):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
    index_path = get_index_html_path()
    if index_path and os.path.isfile(index_path):
        return FileResponse(index_path, media_type="text/html")
    return JSONResponse(status_code=404, content={"detail": "Not Found"})

@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa_fallback(full_path: str, request: Request):
    from pathlib import Path
    clean_path = full_path.strip("/")
    secret_uri = get_panel_secret_uri()

    # Truy cập đúng đường dẫn bí mật -> cấp cookie phiên truy cập cổng & trả giao diện Panel
    if secret_uri and clean_path == secret_uri:
        index_path = get_index_html_path()
        if index_path and os.path.isfile(index_path):
            resp = FileResponse(index_path, media_type="text/html")
            resp.set_cookie(
                key="pp_portal_ok",
                value=_portal_cookie_token(secret_uri),
                httponly=True,
                samesite="lax",
                max_age=86400 * 7
            )
            return resp

    try:
        static_base = Path(settings.STATIC_DIR).resolve()
        candidate = (static_base / full_path).resolve()
        # Chống triệt để Path Traversal ra khỏi STATIC_DIR
        if candidate.is_relative_to(static_base) and candidate.is_file():
            return FileResponse(str(candidate))
    except (ValueError, Exception):
        pass

    if secret_uri and request.cookies.get("pp_portal_ok", "") != _portal_cookie_token(secret_uri):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    index_path = get_index_html_path()
    if index_path and os.path.isfile(index_path):
        return FileResponse(index_path, media_type="text/html")
    return JSONResponse(status_code=404, content={"detail": "Not Found"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)
