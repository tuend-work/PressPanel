from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from app.core.security import verify_password, create_access_token
from app.db.session import get_db_connection

router = APIRouter(prefix="/auth", tags=["Authentication"])

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
async def login(req: LoginRequest):
    db = await get_db_connection()
    cursor = await db.execute("SELECT id, username, password_hash, role, parent_id, status FROM users WHERE username = ?", (req.username,))
    user = await cursor.fetchone()
    await db.close()

    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sai ten dang nhap hoac mat khau")
    
    if user["status"] == "suspended":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tai khoan cua ban da bi khoa")

    token = create_access_token(subject=user["id"], role=user["role"], parent_id=user["parent_id"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user["id"],
        "username": user["username"],
        "role": user["role"]
    }
