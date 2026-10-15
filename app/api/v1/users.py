import os
import re
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional
from app.api.deps import get_current_user, require_role
from app.db.session import get_db_connection
from app.core.security import get_password_hash, create_access_token
from app.executor.gatekeeper import run_gatekeeper

router = APIRouter(prefix="/users", tags=["Customer Management"])

class CustomerCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6)
    email: str = Field(..., min_length=5)
    disk_mb: int = Field(5120, ge=500, le=500000)
    cpu_pct: int = Field(100, ge=10, le=400)
    mem_mb: int = Field(1024, ge=256, le=65536)

@router.get("/customers")
async def list_customers(current_user: dict = Depends(get_current_user)):
    """
    Lấy danh sách các tài khoản Customer (kèm số lượng domains của từng customer).
    Admin xem được tất cả customer; Reseller chỉ xem được customer của mình.
    """
    db = await get_db_connection()
    if current_user["role"] == "admin":
        cursor = await db.execute("""
            SELECT u.id, u.username, u.email, u.role, u.disk_mb, u.cpu_pct, u.mem_mb, u.status, u.created_at,
                   (SELECT COUNT(*) FROM domains d WHERE d.user_id = u.id) as domain_count
            FROM users u
            WHERE u.role = 'user'
            ORDER BY u.created_at DESC
        """)
    elif current_user["role"] == "reseller":
        cursor = await db.execute("""
            SELECT u.id, u.username, u.email, u.role, u.disk_mb, u.cpu_pct, u.mem_mb, u.status, u.created_at,
                   (SELECT COUNT(*) FROM domains d WHERE d.user_id = u.id) as domain_count
            FROM users u
            WHERE u.role = 'user' AND u.parent_id = ?
            ORDER BY u.created_at DESC
        """, (current_user["id"],))
    else:
        await db.close()
        raise HTTPException(status_code=403, detail="Chỉ Admin hoặc Reseller mới có quyền xem danh sách Customer.")

    rows = await cursor.fetchall()
    await db.close()
    return [dict(r) for r in rows]

@router.post("/customers")
async def create_customer(req: CustomerCreate, current_user: dict = Depends(require_role("admin", "reseller"))):
    """
    Tạo tài khoản Customer mới:
    1. Kiểm tra username chỉ gồm a-z, 0-9, _
    2. Gọi gatekeeper/user create <username> <disk_mb> để tạo Linux user & thư mục
    3. Thêm bản ghi vào bảng users của SQLite
    """
    username = req.username.strip().lower()
    if not re.match(r"^[a-z0-9_-]{3,32}$", username):
        raise HTTPException(status_code=400, detail="Tên tài khoản không hợp lệ (3-32 ký tự, chỉ gồm a-z, 0-9, _, -)")

    db = await get_db_connection()
    cursor = await db.execute("SELECT id FROM users WHERE username = ? OR email = ?", (username, req.email.strip()))
    existing = await cursor.fetchone()
    if existing:
        await db.close()
        raise HTTPException(status_code=400, detail="Tên người dùng hoặc email đã tồn tại trong hệ thống")

    # Gọi C gatekeeper: user create <username> <disk_mb>
    res = await run_gatekeeper("user", "create", username, str(req.disk_mb))
    if res.get("status") == "error":
        await db.close()
        raise HTTPException(status_code=400, detail=res.get("message", "Không thể tạo tài khoản Linux hệ thống"))

    # Thiết lập mật khẩu Linux user nếu cần
    try:
        # Lưu vào SQLite
        pwd_hash = get_password_hash(req.password)
        # Lấy package mặc định
        pkg_cur = await db.execute("SELECT id FROM packages WHERE package_type = 'user' LIMIT 1")
        pkg = await pkg_cur.fetchone()
        package_id = pkg["id"] if pkg else 2

        await db.execute("""
            INSERT INTO users (parent_id, username, password_hash, email, role, package_id, cpu_pct, mem_mb, disk_mb, io_weight, pids_max, status)
            VALUES (?, ?, ?, ?, 'user', ?, ?, ?, ?, 100, 100, 'active')
        """, (current_user["id"], username, pwd_hash, req.email.strip(), package_id, req.cpu_pct, req.mem_mb, req.disk_mb))
        await db.commit()

        # Lấy id vừa tạo
        id_cur = await db.execute("SELECT id FROM users WHERE username = ?", (username,))
        created_user = await id_cur.fetchone()
        user_id = created_user["id"]
    except Exception as e:
        await db.close()
        raise HTTPException(status_code=500, detail=f"Lỗi lưu trữ cơ sở dữ liệu: {str(e)}")

    await db.close()
    return {
        "status": "success",
        "message": f"Tạo Customer '{username}' thành công!",
        "customer": {
            "id": user_id,
            "username": username,
            "email": req.email.strip(),
            "role": "user",
            "disk_mb": req.disk_mb,
            "cpu_pct": req.cpu_pct,
            "mem_mb": req.mem_mb
        }
    }

@router.post("/{user_id}/impersonate")
async def impersonate_customer(user_id: int, current_user: dict = Depends(require_role("admin", "reseller"))):
    """
    Chức năng Login As User (Impersonation):
    Admin hoặc Reseller có thể đăng nhập vào tài khoản của Customer để cấu hình website, WordPress, v.v.
    Trả về JWT access_token của Customer đó.
    """
    db = await get_db_connection()
    cursor = await db.execute("SELECT id, username, email, role, parent_id, status FROM users WHERE id = ?", (user_id,))
    target_user = await cursor.fetchone()
    await db.close()

    if not target_user:
        raise HTTPException(status_code=404, detail="Không tìm thấy Customer này")

    if target_user["role"] == "admin":
        raise HTTPException(status_code=400, detail="Không thể Login As vào một tài khoản Admin khác")

    if current_user["role"] == "reseller" and target_user["parent_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Bạn chỉ có thể Login As vào Customer do bạn trực tiếp quản lý")

    # Tạo JWT access_token đại diện cho target_user
    impersonate_token = create_access_token(
        subject=target_user["id"],
        role=target_user["role"],
        parent_id=target_user["parent_id"]
    )

    return {
        "status": "success",
        "impersonated": True,
        "access_token": impersonate_token,
        "token_type": "bearer",
        "customer": {
            "id": target_user["id"],
            "username": target_user["username"],
            "email": target_user["email"],
            "role": target_user["role"]
        }
    }
