import time
from typing import Dict, Tuple
from fastapi import APIRouter, HTTPException, status, Request
from pydantic import BaseModel
from app.core.security import verify_password, create_access_token
from app.db.session import get_db_connection
from app.api.deps import get_current_user

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Cache đếm số lần đăng nhập thất bại: identifier -> (failed_count, lock_until_timestamp)
LOGIN_ATTEMPTS: Dict[str, Tuple[int, float]] = {}
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 900  # 15 phút

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    role: str

@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request):
    forwarded_for = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    client_ip = forwarded_for or (request.client.host if request.client else "unknown")
    lock_key = f"{client_ip}:{req.username.strip().lower()}"
    now = time.time()

    # Dọn dẹp định kỳ các entry đã hết hạn để tránh rò rỉ bộ nhớ
    if len(LOGIN_ATTEMPTS) > 1000:
        expired_keys = [k for k, (cnt, lu) in LOGIN_ATTEMPTS.items() if lu > 0 and now >= lu]
        for k in expired_keys:
            LOGIN_ATTEMPTS.pop(k, None)

    # Kiểm tra trạng thái tạm khóa chống brute-force
    if lock_key in LOGIN_ATTEMPTS:
        count, lock_until = LOGIN_ATTEMPTS[lock_key]
        if now < lock_until:
            wait_mins = max(1, int((lock_until - now) / 60))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Đăng nhập thất bại quá nhiều lần. Tài khoản tạm khóa, vui lòng thử lại sau {wait_mins} phút."
            )
        elif now >= lock_until and count >= MAX_LOGIN_ATTEMPTS:
            LOGIN_ATTEMPTS.pop(lock_key, None)

    db = await get_db_connection()
    try:
        cursor = await db.execute(
            "SELECT id, username, password_hash, role, parent_id, status, COALESCE(token_version, 1) as token_version FROM users WHERE username = ?",
            (req.username.strip(),)
        )
        user = await cursor.fetchone()
    finally:
        await db.close()

    if not user or not verify_password(req.password, user["password_hash"]):
        # Tăng số lần thất bại
        cur_count, _ = LOGIN_ATTEMPTS.get(lock_key, (0, 0.0))
        cur_count += 1
        if cur_count >= MAX_LOGIN_ATTEMPTS:
            LOGIN_ATTEMPTS[lock_key] = (cur_count, now + LOCKOUT_SECONDS)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Đăng nhập sai quá 5 lần. Tạm khóa trong 15 phút để bảo vệ an toàn."
            )
        else:
            LOGIN_ATTEMPTS[lock_key] = (cur_count, 0.0)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sai ten dang nhap hoac mat khau")

    if user["status"] == "suspended":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tai khoan dang bi tam khoa")

    # Đăng nhập thành công -> xóa bỏ theo dõi lỗi
    LOGIN_ATTEMPTS.pop(lock_key, None)

    role = user["role"]
    if role == "reseller":
        role = "user"

    token = create_access_token(
        subject=user["id"],
        role=role,
        parent_id=user["parent_id"],
        tv=user["token_version"]
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user["id"],
        "username": user["username"],
        "role": role
    }
