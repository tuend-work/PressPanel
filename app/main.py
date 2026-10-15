import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.db.init_db import init_database
from app.api.v1.auth import router as auth_router
from app.api.v1.domains import router as domains_router
from app.api.v1.wp import router as wp_router
from app.api.v1.files import router as files_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Khởi động: Tạo cấu trúc SQLite 12 bảng nếu chưa có
    await init_database()
    yield
    # Tắt dịch vụ: cleanup nếu cần

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="PressPanel: Control Panel Tối Ưu WordPress Cho Linux (FastAPI + C Gatekeepers)",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Đăng ký các Router REST API
app.include_router(auth_router, prefix=settings.API_V1_STR)
app.include_router(domains_router, prefix=settings.API_V1_STR)
app.include_router(wp_router, prefix=settings.API_V1_STR)
app.include_router(files_router, prefix=settings.API_V1_STR)

@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok", "project": settings.PROJECT_NAME, "version": settings.VERSION}

# Mount Static UI nếu thư mục static tồn tại (Vue 3 Build)
if os.path.exists(settings.STATIC_DIR):
    app.mount("/", StaticFiles(directory=settings.STATIC_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)
