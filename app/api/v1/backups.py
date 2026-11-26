"""
WPCloud: backups.py - Router Quản Lý Sao Lưu & Kho Lưu Trữ Đám Mây (S3, R2, SFTP)
"""

import os
import shutil
import logging
import asyncio
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from app.api.deps import get_current_user
from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backups", tags=["Backups & Cloud Storage"])

class BackupCreateReq(BaseModel):
    domain_id: int
    destination_id: Optional[int] = 0

class DestinationCreateReq(BaseModel):
    name: str
    storage_type: str  # 's3' | 'sftp'
    endpoint: Optional[str] = ""
    bucket: Optional[str] = ""
    access_key: Optional[str] = ""
    secret_key: Optional[str] = ""
    region: Optional[str] = "us-east-1"
    sftp_host: Optional[str] = ""
    sftp_port: Optional[int] = 22
    sftp_user: Optional[str] = ""
    sftp_pass: Optional[str] = ""
    sftp_path: Optional[str] = "/backups"

class ScheduleCreateReq(BaseModel):
    name: str
    target_type: str = "website"  # 'all' | 'website' | 'db' | 'panel'
    target_id: Optional[int] = 0
    destination_id: Optional[int] = 0
    frequency: str = "daily"  # 'daily' | 'weekly' | 'monthly'
    time_hour: Optional[int] = 2
    time_min: Optional[int] = 0
    retention_count: Optional[int] = 7
    enabled: Optional[bool] = True


@router.get("/list")
async def list_backups(current_user: dict = Depends(get_current_user)):
    """Lấy danh sách các bản sao lưu trong hệ thống"""
    db = await get_db_connection()
    is_admin = (current_user.get("role") == "admin")
    user_id = current_user.get("id")

    query = """
        SELECT b.id, b.user_id, b.domain_id, b.backup_type, b.storage_type,
               b.file_path, b.file_size_bytes, b.status, b.destination_id,
               b.error_msg, b.created_at, d.domain_name, u.username
        FROM backups b
        LEFT JOIN domains d ON b.domain_id = d.id
        LEFT JOIN users u ON b.user_id = u.id
    """
    params = ()
    if not is_admin:
        query += " WHERE b.user_id = ?"
        params = (user_id,)
    query += " ORDER BY b.id DESC"

    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    backups = []
    for r in rows:
        file_name = os.path.basename(r[5]) if r[5] else "backup.tar.gz"
        backups.append({
            "id": r[0],
            "user_id": r[1],
            "domain_id": r[2],
            "backup_type": r[3],
            "storage_type": r[4],
            "file_path": r[5],
            "file_name": file_name,
            "file_size_bytes": r[6] or 0,
            "file_size_mb": round((r[6] or 0) / (1024 * 1024), 2),
            "status": r[7] or "success",
            "destination_id": r[8] or 0,
            "error_msg": r[9] or "",
            "created_at": r[10],
            "domain_name": r[11] or "Toàn bộ Panel",
            "username": r[12] or "admin"
        })

    return {"status": "ok", "backups": backups}


@router.post("/create")
async def create_backup(req: BackupCreateReq, current_user: dict = Depends(get_current_user)):
    """Tạo bản sao lưu Website ngay lập tức"""
    user_id = current_user.get("id")
    is_admin = (current_user.get("role") == "admin")

    db = await get_db_connection()
    c = await db.execute("SELECT user_id, domain_name FROM domains WHERE id = ?", (req.domain_id,))
    dom = await c.fetchone()
    if not dom:
        raise HTTPException(status_code=404, detail="Không tìm thấy website cần sao lưu!")

    if not is_admin and dom[0] != user_id:
        raise HTTPException(status_code=403, detail="Bạn không có quyền sao lưu website này!")

    from app.services.cloud_backup import create_website_backup
    res = await create_website_backup(domain_id=req.domain_id, user_id=dom[0], destination_id=req.destination_id or 0)
    if res.get("status") == "error":
        raise HTTPException(status_code=500, detail=res.get("message", "Lỗi tạo bản sao lưu"))

    return res


@router.delete("/{backup_id}")
async def delete_backup(backup_id: int, current_user: dict = Depends(get_current_user)):
    """Xóa bản sao lưu khỏi hệ thống"""
    user_id = current_user.get("id")
    is_admin = (current_user.get("role") == "admin")

    db = await get_db_connection()
    c = await db.execute("SELECT user_id, file_path FROM backups WHERE id = ?", (backup_id,))
    b = await c.fetchone()
    if not b:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản sao lưu!")

    if not is_admin and b[0] != user_id:
        raise HTTPException(status_code=403, detail="Bạn không có quyền xóa bản sao lưu này!")

    file_path = b[1]
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass

    await db.execute("DELETE FROM backups WHERE id = ?", (backup_id,))
    await db.commit()

    return {"status": "ok", "message": "Đã xóa bản sao lưu thành công!"}


@router.post("/restore/{backup_id}")
async def restore_backup(backup_id: int, current_user: dict = Depends(get_current_user)):
    """Khôi phục Website từ bản sao lưu"""
    user_id = current_user.get("id")
    is_admin = (current_user.get("role") == "admin")

    db = await get_db_connection()
    c = await db.execute("""
        SELECT b.file_path, b.domain_id, b.user_id, d.document_root, d.domain_name
        FROM backups b
        JOIN domains d ON b.domain_id = d.id
        WHERE b.id = ?
    """, (backup_id,))
    b = await c.fetchone()
    if not b:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản sao lưu!")

    if not is_admin and b[2] != user_id:
        raise HTTPException(status_code=403, detail="Bạn không có quyền khôi phục website này!")

    file_path = b[0]
    doc_root = b[3]
    domain_name = b[4]

    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="Tệp tin sao lưu cục bộ không tồn tại trên máy chủ!")

    try:
        # Kiểm tra an toàn file nén chống Tar Slip / Symlink thoát khỏi Document Root
        import tarfile
        with tarfile.open(file_path, "r:gz") as tf:
            base_real = os.path.realpath(doc_root)
            for member in tf.getmembers():
                if member.issym() or member.islnk():
                    raise HTTPException(status_code=400, detail="Tệp sao lưu chứa liên kết symlink không an toàn!")
                target_path = os.path.realpath(os.path.join(base_real, member.name))
                if not target_path.startswith(base_real + os.sep) and target_path != base_real:
                    raise HTTPException(status_code=400, detail="Phát hiện đường dẫn không hợp lệ trong tệp sao lưu (Tar Slip)!")
            tf.extractall(path=doc_root)

        # Nạp lại database nếu có file sql bên trong
        c_wp = await db.execute("SELECT db_name FROM wp_instances WHERE domain_id = ?", (b[1],))
        wp_db = await c_wp.fetchone()
        if wp_db and wp_db[0]:
            db_name = wp_db[0]
            sql_file = os.path.join(doc_root, f"{domain_name}_database.sql")
            if os.path.exists(sql_file):
                import subprocess
                with open(sql_file, "rb") as sf:
                    subprocess.run(["mysql", db_name], stdin=sf, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
                os.remove(sql_file)

        return {"status": "ok", "message": f"Đã khôi phục thành công website {domain_name} từ bản sao lưu!"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khôi phục: {str(e)}")
    finally:
        await db.close()


# ==============================================================================
# DESTINATIONS API (S3, CLOUDFLARE R2, WASABI, SFTP)
# ==============================================================================

@router.get("/destinations")
async def list_destinations(current_user: dict = Depends(get_current_user)):
    """Lấy danh sách các điểm lưu trữ đám mây từ xa"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ Quản trị viên mới có quyền xem điểm lưu trữ!")

    db = await get_db_connection()
    c = await db.execute("SELECT id, name, storage_type, endpoint, bucket, access_key, region, sftp_host, sftp_port, sftp_user, sftp_path, created_at FROM backup_destinations ORDER BY id DESC")
    rows = await c.fetchall()
    dests = []
    for r in rows:
        dests.append({
            "id": r[0],
            "name": r[1],
            "storage_type": r[2],
            "endpoint": r[3] or "",
            "bucket": r[4] or "",
            "access_key": r[5] or "",
            "region": r[6] or "us-east-1",
            "sftp_host": r[7] or "",
            "sftp_port": r[8] or 22,
            "sftp_user": r[9] or "",
            "sftp_path": r[10] or "/backups",
            "created_at": r[11]
        })
    return {"status": "ok", "destinations": dests}


@router.post("/destinations")
async def add_destination(req: DestinationCreateReq, current_user: dict = Depends(get_current_user)):
    """Thêm kho lưu trữ Cloud mới (S3, Cloudflare R2, SFTP)"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ Quản trị viên mới có quyền thêm điểm lưu trữ!")

    name = req.name.strip()
    stype = req.storage_type.lower()
    if not name or stype not in ("s3", "sftp"):
        raise HTTPException(status_code=400, detail="Tên hoặc loại lưu trữ không hợp lệ!")

    db = await get_db_connection()
    await db.execute("""
        INSERT INTO backup_destinations (name, storage_type, endpoint, bucket, access_key, secret_key, region, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (name, stype, req.endpoint, req.bucket, req.access_key, req.secret_key, req.region, req.sftp_host, req.sftp_port, req.sftp_user, req.sftp_pass, req.sftp_path))
    await db.commit()

    return {"status": "ok", "message": f"Đã thêm điểm lưu trữ Cloud '{name}' thành công!"}


@router.delete("/destinations/{dest_id}")
async def delete_destination(dest_id: int, current_user: dict = Depends(get_current_user)):
    """Xóa điểm lưu trữ Cloud"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ Quản trị viên mới có quyền xóa điểm lưu trữ!")

    db = await get_db_connection()
    await db.execute("DELETE FROM backup_destinations WHERE id = ?", (dest_id,))
    await db.commit()
    return {"status": "ok", "message": "Đã xóa điểm lưu trữ thành công!"}


# ==============================================================================
# SCHEDULES API (AUTO BACKUP & RETENTION)
# ==============================================================================

@router.get("/schedules")
async def list_schedules(current_user: dict = Depends(get_current_user)):
    """Lấy danh sách các lịch sao lưu tự động"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ Quản trị viên mới có quyền xem lịch sao lưu!")

    db = await get_db_connection()
    c = await db.execute("""
        SELECT s.id, s.name, s.target_type, s.target_id, s.destination_id,
               s.frequency, s.time_hour, s.time_min, s.retention_count, s.enabled,
               s.last_run, s.created_at, bd.name as dest_name
        FROM backup_schedules s
        LEFT JOIN backup_destinations bd ON s.destination_id = bd.id
        ORDER BY s.id DESC
    """)
    rows = await c.fetchall()
    schedules = []
    for r in rows:
        schedules.append({
            "id": r[0],
            "name": r[1],
            "target_type": r[2],
            "target_id": r[3],
            "destination_id": r[4],
            "frequency": r[5],
            "time_hour": r[6],
            "time_min": r[7],
            "retention_count": r[8],
            "enabled": bool(r[9]),
            "last_run": r[10] or "Chưa chạy",
            "created_at": r[11],
            "destination_name": r[12] or "Lưu cục bộ (Local)"
        })
    return {"status": "ok", "schedules": schedules}


@router.post("/schedules")
async def add_schedule(req: ScheduleCreateReq, current_user: dict = Depends(get_current_user)):
    """Tạo lịch sao lưu tự động định kỳ"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ Quản trị viên mới có quyền tạo lịch sao lưu!")

    db = await get_db_connection()
    await db.execute("""
        INSERT INTO backup_schedules (name, target_type, target_id, destination_id, frequency, time_hour, time_min, retention_count, enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (req.name.strip(), req.target_type, req.target_id, req.destination_id, req.frequency, req.time_hour, req.time_min, req.retention_count, 1 if req.enabled else 0))
    await db.commit()

    return {"status": "ok", "message": f"Đã thiết lập lịch sao lưu tự động '{req.name}' thành công!"}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: int, current_user: dict = Depends(get_current_user)):
    """Xóa lịch sao lưu tự động"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ Quản trị viên mới có quyền xóa lịch sao lưu!")

    db = await get_db_connection()
    await db.execute("DELETE FROM backup_schedules WHERE id = ?", (schedule_id,))
    await db.commit()
    return {"status": "ok", "message": "Đã xóa lịch sao lưu thành công!"}
