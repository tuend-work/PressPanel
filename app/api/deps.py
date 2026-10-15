from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.security import decode_token
from app.db.session import get_db_connection

security_bearer = HTTPBearer()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security_bearer)) -> dict:
    token = credentials.credentials
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token xac thuc khong hop le hoac da het han",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id = int(payload["sub"])
    db = await get_db_connection()
    cursor = await db.execute("SELECT id, username, email, role, parent_id, status FROM users WHERE id = ?", (user_id,))
    user = await cursor.fetchone()
    await db.close()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Khong tim thay nguoi dung")
    if user["status"] == "suspended":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tai khoan dang bi khoa")

    return dict(user)

def require_role(*allowed_roles: str):
    def role_checker(current_user: dict = Depends(get_current_user)):
        if current_user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Quyen truy cap bi tu choi. Yeu cau vai tro: {', '.join(allowed_roles)}"
            )
        return current_user
    return role_checker
