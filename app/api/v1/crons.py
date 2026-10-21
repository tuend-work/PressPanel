import os
import subprocess
import asyncio
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.db.session import get_db_connection

router = APIRouter(prefix="/crons", tags=["Cronjobs Manager"])


class CronJobCreate(BaseModel):
    schedule: str = Field(..., description="Cú pháp Cron 5 trường: '*/15 * * * *'")
    command: str = Field(..., description="Câu lệnh thực thi")
    name: Optional[str] = Field("", description="Tên gợi nhớ hoặc mô tả tác vụ")
    status: Optional[str] = Field("active", description="Trạng thái: active hoặc disabled")
    domain_id: Optional[int] = Field(None, description="Website liên kết nếu có")
    target_user: Optional[str] = Field(None, description="Tài khoản Linux thực thi (dành cho Admin)")


class CronJobUpdate(BaseModel):
    schedule: Optional[str] = None
    command: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None


def validate_cron_schedule(sched: str) -> bool:
    parts = sched.strip().split()
    return len(parts) in (5, 6)


async def sync_user_crontab(username: str, db):
    """Đồng bộ toàn bộ cronjob đang active của một user vào Linux crontab"""
    if not username or username == "root":
        return

    cursor = await db.execute("""
        SELECT c.id, c.schedule, c.command, c.status
        FROM cron_jobs c
        JOIN users u ON c.user_id = u.id
        WHERE u.username = ? AND c.status = 'active'
    """, (username,))
    jobs = await cursor.fetchall()

    lines = [
        f"# WPCloud Managed Crontab for user: {username}",
        "# Do not edit lines between WPC markers manually",
        "# ==================== WPC-CRON-BEGIN ===================="
    ]
    for j in jobs:
        cmd = j["command"].strip()
        lines.append(f"{j['schedule']} {cmd}")
    lines.append("# ==================== WPC-CRON-END ====================\n")

    content = "\n".join(lines)

    # Thử nạp vào crontab
    try:
        if os.name != 'nt':
            proc = subprocess.run(
                ["crontab", "-u", username, "-"],
                input=content.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False
            )
            if proc.returncode != 0:
                # Fallback ghi trực tiếp vào /var/spool/cron/crontabs/
                spool_dir = "/var/spool/cron/crontabs"
                if os.path.exists(spool_dir):
                    user_cron = os.path.join(spool_dir, username)
                    with open(user_cron, "w", encoding="utf-8") as f:
                        f.write(content)
                    subprocess.run(["chmod", "600", user_cron], check=False)
                    subprocess.run(["chown", f"{username}:crontab", user_cron], check=False)
    except Exception:
        pass


@router.get("")
async def list_cron_jobs(
    domain_id: Optional[int] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Lấy danh sách cronjob theo quyền hạn của tài khoản đăng nhập"""
    db = await get_db_connection()
    is_admin = (current_user["role"] == "admin" or current_user["username"] == "admin")
    is_web_user = "web_domain_id" in current_user and current_user.get("web_domain_id") is not None
    user_id = current_user["id"]

    try:
        # Đảm bảo bảng có cột name và domain_id nếu chưa có
        try:
            await db.execute("ALTER TABLE cron_jobs ADD COLUMN name TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE cron_jobs ADD COLUMN domain_id INTEGER DEFAULT NULL")
            await db.commit()
        except Exception:
            pass

        if is_admin:
            if domain_id:
                cursor = await db.execute("""
                    SELECT c.*, u.username as owner_username, d.domain_name
                    FROM cron_jobs c
                    JOIN users u ON c.user_id = u.id
                    LEFT JOIN domains d ON c.domain_id = d.id
                    WHERE c.domain_id = ?
                    ORDER BY c.id DESC
                """, (domain_id,))
            else:
                cursor = await db.execute("""
                    SELECT c.*, u.username as owner_username, d.domain_name
                    FROM cron_jobs c
                    JOIN users u ON c.user_id = u.id
                    LEFT JOIN domains d ON c.domain_id = d.id
                    ORDER BY c.id DESC
                """)
        elif is_web_user:
            web_domain_id = current_user.get("web_domain_id")
            cursor = await db.execute("""
                SELECT c.*, u.username as owner_username, d.domain_name
                FROM cron_jobs c
                JOIN users u ON c.user_id = u.id
                LEFT JOIN domains d ON c.domain_id = d.id
                WHERE c.domain_id = ? OR c.user_id = ?
                ORDER BY c.id DESC
            """, (web_domain_id, user_id))
        else:
            # Hosting User: xem cronjob của mình và các website trực thuộc
            cursor = await db.execute("""
                SELECT c.*, u.username as owner_username, d.domain_name
                FROM cron_jobs c
                JOIN users u ON c.user_id = u.id
                LEFT JOIN domains d ON c.domain_id = d.id
                WHERE c.user_id = ? OR u.parent_id = ? OR d.user_id = ?
                ORDER BY c.id DESC
            """, (user_id, user_id, user_id))

        jobs = await cursor.fetchall()
        result = [dict(j) for j in jobs]
        return {"status": "success", "data": result}
    finally:
        await db.close()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_cron_job(
    req: CronJobCreate,
    current_user: dict = Depends(get_current_user)
):
    """Tạo mới một Cronjob cho Website hoặc Hosting User"""
    if not validate_cron_schedule(req.schedule):
        raise HTTPException(
            status_code=400,
            detail="Cú pháp lịch biểu Cron không hợp lệ (cần đúng 5 trường, ví dụ: '*/15 * * * *')"
        )
    if not req.command or not req.command.strip():
        raise HTTPException(status_code=400, detail="Câu lệnh thực thi không được để trống")

    db = await get_db_connection()
    is_admin = (current_user["role"] == "admin" or current_user["username"] == "admin")
    is_web_user = "web_domain_id" in current_user and current_user.get("web_domain_id") is not None

    try:
        target_user_id = current_user["id"]
        target_username = current_user["username"]
        assigned_domain_id = req.domain_id

        if is_web_user:
            assigned_domain_id = current_user.get("web_domain_id")
            target_username = current_user.get("web_user_name", current_user["username"])
        elif is_admin and req.target_user:
            # Admin chỉ định user
            cursor = await db.execute("SELECT id, username FROM users WHERE username = ?", (req.target_user.strip(),))
            u_found = await cursor.fetchone()
            if u_found:
                target_user_id = u_found["id"]
                target_username = u_found["username"]
        elif req.domain_id:
            # Hosting user chọn một domain
            cursor = await db.execute("SELECT id, user_id FROM domains WHERE id = ?", (req.domain_id,))
            d_found = await cursor.fetchone()
            if d_found:
                assigned_domain_id = d_found["id"]

        cursor = await db.execute("""
            INSERT INTO cron_jobs (user_id, domain_id, name, schedule, command, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            target_user_id,
            assigned_domain_id,
            req.name or f"Tác vụ #{req.schedule}",
            req.schedule.strip(),
            req.command.strip(),
            req.status or "active"
        ))
        new_id = cursor.lastrowid
        await db.commit()

        # Đồng bộ crontab hệ điều hành
        await sync_user_crontab(target_username, db)

        return {
            "status": "success",
            "message": "Đã tạo Cronjob thành công!",
            "id": new_id
        }
    finally:
        await db.close()


@router.put("/{cron_id}")
async def update_cron_job(
    cron_id: int,
    req: CronJobUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Cập nhật Cronjob"""
    db = await get_db_connection()
    is_admin = (current_user["role"] == "admin" or current_user["username"] == "admin")
    user_id = current_user["id"]

    try:
        cursor = await db.execute("""
            SELECT c.*, u.username as owner_username, u.parent_id
            FROM cron_jobs c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = ?
        """, (cron_id,))
        job = await cursor.fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy Cronjob này")

        if not is_admin:
            if job["user_id"] != user_id and job["parent_id"] != user_id:
                raise HTTPException(status_code=403, detail="Không có quyền sửa đổi Cronjob này")

        new_schedule = req.schedule.strip() if req.schedule else job["schedule"]
        if req.schedule and not validate_cron_schedule(new_schedule):
            raise HTTPException(status_code=400, detail="Cú pháp lịch biểu Cron không hợp lệ")

        new_command = req.command.strip() if req.command else job["command"]
        new_name = req.name if req.name is not None else job["name"]
        new_status = req.status if req.status in ("active", "disabled") else job["status"]

        await db.execute("""
            UPDATE cron_jobs
            SET schedule = ?, command = ?, name = ?, status = ?
            WHERE id = ?
        """, (new_schedule, new_command, new_name, new_status, cron_id))
        await db.commit()

        await sync_user_crontab(job["owner_username"], db)

        return {"status": "success", "message": "Đã cập nhật Cronjob thành công!"}
    finally:
        await db.close()


@router.delete("/{cron_id}")
async def delete_cron_job(
    cron_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Xóa Cronjob"""
    db = await get_db_connection()
    is_admin = (current_user["role"] == "admin" or current_user["username"] == "admin")
    user_id = current_user["id"]

    try:
        cursor = await db.execute("""
            SELECT c.*, u.username as owner_username, u.parent_id
            FROM cron_jobs c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = ?
        """, (cron_id,))
        job = await cursor.fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy Cronjob này")

        if not is_admin:
            if job["user_id"] != user_id and job["parent_id"] != user_id:
                raise HTTPException(status_code=403, detail="Không có quyền xóa Cronjob này")

        await db.execute("DELETE FROM cron_jobs WHERE id = ?", (cron_id,))
        await db.commit()

        await sync_user_crontab(job["owner_username"], db)

        return {"status": "success", "message": "Đã xóa Cronjob thành công!"}
    finally:
        await db.close()


@router.post("/{cron_id}/toggle")
async def toggle_cron_job(
    cron_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Bật / Tắt trạng thái Cronjob"""
    db = await get_db_connection()
    is_admin = (current_user["role"] == "admin" or current_user["username"] == "admin")
    user_id = current_user["id"]

    try:
        cursor = await db.execute("""
            SELECT c.*, u.username as owner_username, u.parent_id
            FROM cron_jobs c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = ?
        """, (cron_id,))
        job = await cursor.fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy Cronjob này")

        if not is_admin:
            if job["user_id"] != user_id and job["parent_id"] != user_id:
                raise HTTPException(status_code=403, detail="Không có quyền thay đổi trạng thái Cronjob này")

        next_status = "disabled" if job["status"] == "active" else "active"
        await db.execute("UPDATE cron_jobs SET status = ? WHERE id = ?", (next_status, cron_id))
        await db.commit()

        await sync_user_crontab(job["owner_username"], db)

        status_label = "kích hoạt" if next_status == "active" else "tạm dừng"
        return {
            "status": "success",
            "message": f"Đã {status_label} Cronjob thành công!",
            "new_status": next_status
        }
    finally:
        await db.close()


@router.post("/{cron_id}/run")
async def run_cron_job_now(
    cron_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Chạy thử Cronjob ngay lập tức và trả về kết quả output"""
    db = await get_db_connection()
    is_admin = (current_user["role"] == "admin" or current_user["username"] == "admin")
    user_id = current_user["id"]

    try:
        cursor = await db.execute("""
            SELECT c.*, u.username as owner_username, u.parent_id
            FROM cron_jobs c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = ?
        """, (cron_id,))
        job = await cursor.fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy Cronjob này")

        if not is_admin:
            if job["user_id"] != user_id and job["parent_id"] != user_id:
                raise HTTPException(status_code=403, detail="Không có quyền thực thi Cronjob này")

        cmd = job["command"].strip()
        target_username = job["owner_username"]

        # Thực thi lệnh dưới quyền user
        if os.name == 'nt':
            # Chạy giả lập trên Windows dev
            proc = subprocess.run(
                ["cmd.exe", "/c", cmd],
                capture_output=True,
                text=True,
                timeout=30,
                check=False
            )
        else:
            if os.getuid() == 0:
                run_cmd = ["su", "-s", "/bin/bash", target_username, "-c", cmd]
            else:
                run_cmd = ["sudo", "-n", "-u", target_username, "/bin/bash", "-c", cmd]

            proc = subprocess.run(
                run_cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=False
            )

        output = (proc.stdout or "") + (proc.stderr or "")
        return {
            "status": "success" if proc.returncode == 0 else "error",
            "exit_code": proc.returncode,
            "output": output or "(Tác vụ kết thúc mà không xuất ra màn hình)"
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "exit_code": 124,
            "output": "Quá thời gian thực thi cho phép (Timeout 60s)"
        }
    except Exception as e:
        return {
            "status": "error",
            "exit_code": 1,
            "output": f"Lỗi thực thi: {str(e)}"
        }
    finally:
        await db.close()
