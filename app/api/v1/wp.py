from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.api.deps import get_current_user
from app.db.session import get_db_connection
from app.executor.gatekeeper import run_gatekeeper

router = APIRouter(prefix="/wp", tags=["WordPress Toolkit"])

class WPInstallRequest(BaseModel):
    domain_id: int
    site_title: str
    admin_user: str
    admin_pass: str
    admin_email: str

@router.post("/install")
async def install_wordpress(req: WPInstallRequest, current_user: dict = Depends(get_current_user)):
    db = await get_db_connection()
    cursor = await db.execute("SELECT domain_name FROM domains WHERE id = ? AND user_id = ?", (req.domain_id, current_user["id"]))
    domain = await cursor.fetchone()
    if not domain:
        await db.close()
        raise HTTPException(status_code=404, detail="Khong tim thay ten mien")

    domain_name = domain["domain_name"]
    username = current_user["username"]

    # Gọi C gatekeeper: wp install <username> <domain> <title> <admin_user> (pass qua stdin)
    res = await run_gatekeeper("wp", "install", username, domain_name, req.site_title, req.admin_user, stdin_data=req.admin_pass)
    if res.get("status") == "error":
        await db.close()
        raise HTTPException(status_code=400, detail=res.get("message", "Cai dat WordPress that bai"))

    db_name = f"wp_{username[:8]}_{domain_name[:8].replace('.', '_')}"
    await db.execute("""
        INSERT INTO wp_instances (domain_id, site_title, admin_user, admin_email, wp_version, db_name, db_user)
        VALUES (?, ?, ?, ?, 'latest', ?, ?)
    """, (req.domain_id, req.site_title, req.admin_user, req.admin_email, db_name, username))
    await db.commit()
    await db.close()

    return {"status": "ok", "message": f"WordPress da duoc cai dat thanh cong tren {domain_name}!"}

@router.get("/{domain_id}/sso")
async def get_sso_url(domain_id: int, current_user: dict = Depends(get_current_user)):
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT d.domain_name, w.admin_user FROM domains d
        JOIN wp_instances w ON d.id = w.domain_id
        WHERE d.id = ? AND d.user_id = ?
    """, (domain_id, current_user["id"]))
    row = await cursor.fetchone()
    await db.close()

    if not row:
        raise HTTPException(status_code=404, detail="Khong tim thay WordPress instance")

    # Gọi C gatekeeper: wp sso <username> <domain> <admin_user>
    res = await run_gatekeeper("wp", "sso", current_user["username"], row["domain_name"], row["admin_user"])
    return res
