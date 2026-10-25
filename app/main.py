import os
from contextlib import asynccontextmanager

# Đảm bảo tiến trình không bị kẹt ở thư mục /root gây lỗi PermissionError khi gọi subprocess
try:
    if os.path.isdir("/opt/wpc") and os.access("/opt/wpc", os.R_OK):
        os.chdir("/opt/wpc")
    elif os.path.isdir("/tmp"):
        os.chdir("/tmp")
except Exception:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse

from app.core.config import settings
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
from app.api.v1.crons import router as crons_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Khởi động: Tạo cấu trúc SQLite 12 bảng nếu chưa có
    await init_database()
    yield
    # Tắt dịch vụ: cleanup nếu cần

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="WPC: Control Panel Tối Ưu WordPress Cho Linux (FastAPI + C Gatekeepers)",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS Middleware (Bảo mật: Không dùng wildcard kết hợp allow_credentials)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
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

# Mount Static UI nếu thư mục static tồn tại
if os.path.exists(settings.STATIC_DIR):
    app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")

@app.get("/", include_in_schema=False)
async def serve_root():
    index_path = get_index_html_path()
    if index_path and os.path.isfile(index_path):
        return FileResponse(index_path, media_type="text/html")
    return RedirectResponse(url="/docs")

@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa_fallback(full_path: str):
    from pathlib import Path
    try:
        static_base = Path(settings.STATIC_DIR).resolve()
        candidate = (static_base / full_path).resolve()
        # Chống triệt để Path Traversal ra khỏi STATIC_DIR
        if candidate.is_relative_to(static_base) and candidate.is_file():
            return FileResponse(str(candidate))
    except (ValueError, Exception):
        pass

    index_path = get_index_html_path()
    if index_path and os.path.isfile(index_path):
        return FileResponse(index_path, media_type="text/html")
    return RedirectResponse(url="/docs")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)
