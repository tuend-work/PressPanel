from fastapi import APIRouter, Depends, HTTPException
from typing import List
from pydantic import BaseModel
from app.api.deps import get_current_user
from app.db.session import get_db_connection
from app.executor.gatekeeper import run_gatekeeper

router = APIRouter(prefix="/domains", tags=["Websites & Domains"])

class DomainCreate(BaseModel):
    domain_name: str
    webserver: str = "ols"
    php_version: str = "8.2"
    install_wp: bool = False

@router.get("/")
async def list_domains(current_user: dict = Depends(get_current_user)):
    db = await get_db_connection()
    if current_user["role"] == "admin":
        cursor = await db.execute("SELECT * FROM domains ORDER BY created_at DESC")
    elif current_user["role"] == "reseller":
        cursor = await db.execute("""
            SELECT d.* FROM domains d
            JOIN users u ON d.user_id = u.id
            WHERE u.id = ? OR u.parent_id = ?
            ORDER BY d.created_at DESC
        """, (current_user["id"], current_user["id"]))
    else:
        cursor = await db.execute("SELECT * FROM domains WHERE user_id = ? ORDER BY created_at DESC", (current_user["id"],))
    
    rows = await cursor.fetchall()
    await db.close()
    return [dict(r) for r in rows]

@router.post("/")
async def create_domain(req: DomainCreate, current_user: dict = Depends(get_current_user)):
    username = current_user["username"]
    doc_root = f"/home/{username}/httpdocs/{req.domain_name}"

    # Gọi C gatekeeper: vhost add <user> <domain> <ols|nginx> <php_ver>
    res = await run_gatekeeper("vhost", "add", username, req.domain_name, req.webserver, req.php_version)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "Khong the tao VirtualHost"))

    db = await get_db_connection()
    try:
        await db.execute("""
            INSERT INTO domains (user_id, domain_name, document_root, webserver, php_version)
            VALUES (?, ?, ?, ?, ?)
        """, (current_user["id"], req.domain_name, doc_root, req.webserver, req.php_version))
        await db.commit()
    except Exception as e:
        await db.close()
        raise HTTPException(status_code=400, detail=f"Domain da ton tai: {str(e)}")
    
    await db.close()

    # Tự động khởi tạo WordPress nếu được yêu cầu
    if req.install_wp:
        await run_gatekeeper("wp", "install", username, req.domain_name)

    return {"status": "ok", "message": f"Da tao website {req.domain_name} thanh cong!"}
