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
    cursor = await db.execute("""
        SELECT u.id, u.username, u.email, COALESCE(u.full_name, '') as full_name, COALESCE(u.phone, '') as phone,
               u.role, u.parent_id, u.status, u.package_id, u.disk_mb, u.cpu_pct, u.mem_mb,
               p.name as package_name, p.max_domains, p.max_databases, COALESCE(p.allow_ssh, 1) as allow_ssh
        FROM users u
        LEFT JOIN packages p ON u.package_id = p.id
        WHERE u.id = ?
    """, (user_id,))
    user = await cursor.fetchone()
    await db.close()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Khong tim thay nguoi dung")
    if user["status"] == "suspended":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tai khoan dang bi khoa")

    user_dict = dict(user)
    # Nếu token được cấp cho vai trò 'user' (Web User của domain cụ thể)
    if payload.get("role") == "user":
        user_dict["role"] = "user"
        if "web_domain_id" in payload:
            user_dict["web_domain_id"] = payload["web_domain_id"]
            db2 = await get_db_connection()
            try:
                d_cur = await db2.execute("""
                    SELECT d.domain_name, d.document_root, d.package_id,
                           p.name as package_name, p.cpu_pct, p.mem_mb, p.disk_mb
                    FROM domains d
                    LEFT JOIN packages p ON d.package_id = p.id
                    WHERE d.id = ?
                """, (payload["web_domain_id"],))
                d_row = await d_cur.fetchone()
                if d_row:
                    if d_row["package_name"]:
                        user_dict["package_name"] = d_row["package_name"]
                    if d_row["cpu_pct"] is not None:
                        user_dict["cpu_pct"] = d_row["cpu_pct"]
                    if d_row["mem_mb"] is not None:
                        user_dict["mem_mb"] = d_row["mem_mb"]
                    if d_row["disk_mb"] is not None:
                        user_dict["disk_mb"] = d_row["disk_mb"]
                    user_dict["domain_name"] = d_row["domain_name"]
                    user_dict["document_root"] = d_row["document_root"]
            finally:
                await db2.close()
        if "web_user_name" in payload:
            user_dict["username"] = payload["web_user_name"]

    return user_dict

def require_role(*allowed_roles: str):
    def role_checker(current_user: dict = Depends(get_current_user)):
        if current_user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Quyen truy cap bi tu choi. Yeu cau vai tro: {', '.join(allowed_roles)}"
            )
        return current_user
    return role_checker
