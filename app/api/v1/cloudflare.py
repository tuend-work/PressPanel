import os
import re
import json
import asyncio
import logging
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from app.api.deps import get_current_user
from app.db.session import get_db_connection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cloudflare", tags=["Cloudflare Intelligent"])


class CFConnectRequest(BaseModel):
    domain_id: int
    api_token: str
    zone_id: Optional[str] = ""


class CFDnsRecordRequest(BaseModel):
    domain_id: int
    type: str = "A"
    name: str
    content: str
    ttl: int = 1
    proxied: bool = True
    priority: Optional[int] = None


class CFPurgeCacheRequest(BaseModel):
    domain_id: int
    purge_everything: bool = True
    urls: Optional[List[str]] = None


class CFDevModeRequest(BaseModel):
    domain_id: int
    enabled: bool


class CFFail2banConfigRequest(BaseModel):
    domain_id: int
    enabled: bool
    interval_sec: int = 60
    ban_mode: str = "block"
    max_ips: int = 50


class CFBlockIpRequest(BaseModel):
    domain_id: int
    ip: str
    mode: str = "block"
    note: Optional[str] = "PressPanel Cloudflare Intelligent"


class CFZoneSettingsRequest(BaseModel):
    domain_id: int
    ssl_mode: Optional[str] = None
    always_use_https: Optional[str] = None
    security_level: Optional[str] = None
    brotli: Optional[str] = None
    browser_cache_ttl: Optional[int] = None
    browser_check: Optional[str] = None
    challenge_ttl: Optional[int] = None
    hotlink_protection: Optional[str] = None
    email_obfuscation: Optional[str] = None
    bot_fight_mode: Optional[bool] = None


class CFUnderAttackRequest(BaseModel):
    domain_id: int
    enabled: bool
    fallback_level: str = "medium"


async def _ensure_cf_tables(db):
    await db.execute("""
        CREATE TABLE IF NOT EXISTS cloudflare_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain_id INTEGER UNIQUE NOT NULL,
            api_token TEXT NOT NULL,
            zone_id TEXT DEFAULT '',
            zone_name TEXT DEFAULT '',
            f2b_sync_enabled INTEGER DEFAULT 0,
            f2b_sync_interval INTEGER DEFAULT 60,
            f2b_ban_mode TEXT DEFAULT 'block',
            f2b_max_ips INTEGER DEFAULT 50,
            last_sync_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS cloudflare_blocked_ips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain_id INTEGER NOT NULL,
            ip TEXT NOT NULL,
            mode TEXT DEFAULT 'block',
            cf_rule_id TEXT DEFAULT '',
            source TEXT DEFAULT 'manual',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.commit()


def _cf_request(token: str, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"https://api.cloudflare.com/client/v4{path}"
    headers = {
        "Authorization": f"Bearer {token.strip()}",
        "Content-Type": "application/json",
        "User-Agent": "PressPanel-Cloudflare-Intelligent/1.0"
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except urllib.error.HTTPError as he:
        body = he.read().decode("utf-8", errors="ignore")
        try:
            err_json = json.loads(body)
            msgs = [e.get("message", "") for e in (err_json.get("errors") or [])]
            err_msg = "; ".join([m for m in msgs if m]) or f"Cloudflare HTTP {he.code}"
        except Exception:
            err_msg = f"Cloudflare HTTP {he.code}: {body[:200]}"
        raise HTTPException(status_code=400, detail=err_msg)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Không thể kết nối Cloudflare API: {str(e)}")


def _extract_root_domain(domain_name: str) -> str:
    clean = re.sub(r"^(https?://)?(www\.)?", "", (domain_name or "").strip().lower()).split("/")[0]
    parts = [p for p in clean.split(".") if p]
    if len(parts) <= 2:
        return clean
    if len(parts[-1]) == 2 and parts[-2] in ("com", "net", "org", "edu", "gov", "vn", "co", "ac"):
        return ".".join(parts[-3:]) if len(parts) >= 3 else clean
    return ".".join(parts[-2:])


async def _get_allowed_domains(db, current_user: dict) -> List[Dict[str, Any]]:
    if current_user.get("role") == "admin":
        cur = await db.execute("SELECT id, domain_name, user_id FROM domains WHERE is_system = 0 OR is_system IS NULL ORDER BY id ASC")
    elif current_user.get("web_domain_id"):
        cur = await db.execute("SELECT id, domain_name, user_id FROM domains WHERE id = ?", (current_user["web_domain_id"],))
    else:
        cur = await db.execute("""
            SELECT d.id, d.domain_name, d.user_id
            FROM domains d
            LEFT JOIN users u ON d.user_id = u.id
            WHERE d.user_id = ? OR u.parent_id = ?
            ORDER BY d.id ASC
        """, (current_user["id"], current_user["id"]))
    return [dict(r) for r in await cur.fetchall()]


async def _resolve_domain_and_cf(domain_id: int, current_user: dict):
    db = await get_db_connection()
    await _ensure_cf_tables(db)
    allowed = await _get_allowed_domains(db, current_user)
    target = next((d for d in allowed if d["id"] == domain_id), None)
    if not target:
        await db.close()
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập domain này")

    cur = await db.execute("SELECT * FROM cloudflare_configs WHERE domain_id = ?", (domain_id,))
    row = await cur.fetchone()
    await db.close()
    return target, (dict(row) if row else None), allowed


@router.get("/config")
async def get_cloudflare_config(domain_id: Optional[int] = Query(None), current_user: dict = Depends(get_current_user)):
    db = await get_db_connection()
    await _ensure_cf_tables(db)
    allowed = await _get_allowed_domains(db, current_user)
    if not allowed:
        await db.close()
        return {"status": "ok", "domains": [], "config": None}

    target_id = domain_id if domain_id and any(d["id"] == domain_id for d in allowed) else allowed[0]["id"]
    cur = await db.execute("SELECT * FROM cloudflare_configs WHERE domain_id = ?", (target_id,))
    row = await cur.fetchone()
    await db.close()

    cfg = None
    if row:
        cfg = dict(row)
        tok = cfg.pop("api_token", "")
        cfg["has_token"] = bool(tok)
        cfg["token_preview"] = (tok[:4] + "..." + tok[-4:]) if len(tok) > 10 else ("***" if tok else "")

    return {
        "status": "ok",
        "domain_id": target_id,
        "domains": allowed,
        "config": cfg
    }


@router.post("/connect")
async def connect_cloudflare(req: CFConnectRequest, current_user: dict = Depends(get_current_user)):
    target, existing_cfg, _ = await _resolve_domain_and_cf(req.domain_id, current_user)
    token = req.api_token.strip()
    if not token and existing_cfg:
        token = existing_cfg["api_token"]
    if not token:
        raise HTTPException(status_code=400, detail="Vui lòng nhập Cloudflare API Token")

    # 1. Xác thực API Token
    verify_res = _cf_request(token, "GET", "/user/tokens/verify")
    if not verify_res.get("success"):
        raise HTTPException(status_code=400, detail="Cloudflare API Token không hợp lệ hoặc đã hết hạn")

    # 2. Tìm Zone ID theo domain nếu chưa nhập thủ công
    zone_id = (req.zone_id or "").strip()
    zone_name = _extract_root_domain(target["domain_name"])
    zone_info = {}

    if not zone_id:
        z_res = _cf_request(token, "GET", f"/zones?name={urllib.parse.quote(zone_name)}")
        zones = z_res.get("result") or []
        if not zones:
            # Thử tìm theo full domain_name
            z_res2 = _cf_request(token, "GET", f"/zones?name={urllib.parse.quote(target['domain_name'])}")
            zones = z_res2.get("result") or []
        if not zones:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy Zone '{zone_name}' trong tài khoản Cloudflare của Token này. Bạn có thể nhập Zone ID thủ công.")
        zone_id = zones[0]["id"]
        zone_name = zones[0]["name"]
        zone_info = zones[0]
    else:
        z_res = _cf_request(token, "GET", f"/zones/{urllib.parse.quote(zone_id)}")
        zone_info = z_res.get("result") or {}
        if zone_info.get("name"):
            zone_name = zone_info["name"]

    db = await get_db_connection()
    await _ensure_cf_tables(db)
    await db.execute("""
        INSERT INTO cloudflare_configs (domain_id, api_token, zone_id, zone_name, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(domain_id) DO UPDATE SET
            api_token = excluded.api_token,
            zone_id = excluded.zone_id,
            zone_name = excluded.zone_name,
            updated_at = CURRENT_TIMESTAMP
    """, (req.domain_id, token, zone_id, zone_name))
    await db.commit()
    await db.close()

    return {
        "status": "ok",
        "message": f"Đã kết nối Cloudflare Zone '{zone_name}' thành công!",
        "zone_id": zone_id,
        "zone_name": zone_name,
        "plan": (zone_info.get("plan") or {}).get("name", "Free"),
        "name_servers": zone_info.get("name_servers") or []
    }


@router.delete("/disconnect")
async def disconnect_cloudflare(domain_id: int = Query(...), current_user: dict = Depends(get_current_user)):
    await _resolve_domain_and_cf(domain_id, current_user)
    db = await get_db_connection()
    await db.execute("DELETE FROM cloudflare_configs WHERE domain_id = ?", (domain_id,))
    await db.commit()
    await db.close()
    return {"status": "ok", "message": "Đã ngắt kết nối Cloudflare cho domain này."}


def _require_cf_connected(cfg: Optional[Dict[str, Any]]):
    if not cfg or not cfg.get("api_token") or not cfg.get("zone_id"):
        raise HTTPException(status_code=400, detail="Domain chưa kết nối Cloudflare API Token / Zone ID.")
    return cfg["api_token"], cfg["zone_id"]


@router.get("/dns")
async def list_cf_dns(domain_id: int = Query(...), current_user: dict = Depends(get_current_user)):
    _, cfg, _ = await _resolve_domain_and_cf(domain_id, current_user)
    token, zone_id = _require_cf_connected(cfg)
    res = _cf_request(token, "GET", f"/zones/{zone_id}/dns_records?per_page=100")
    return {"status": "ok", "records": res.get("result") or []}


@router.post("/dns")
async def create_cf_dns(req: CFDnsRecordRequest, current_user: dict = Depends(get_current_user)):
    _, cfg, _ = await _resolve_domain_and_cf(req.domain_id, current_user)
    token, zone_id = _require_cf_connected(cfg)
    payload: Dict[str, Any] = {
        "type": req.type.upper().strip(),
        "name": req.name.strip(),
        "content": req.content.strip(),
        "ttl": int(req.ttl or 1),
        "proxied": bool(req.proxied) if req.type.upper() in ("A", "AAAA", "CNAME") else False
    }
    if req.type.upper() == "MX" and req.priority is not None:
        payload["priority"] = int(req.priority)
    res = _cf_request(token, "POST", f"/zones/{zone_id}/dns_records", payload)
    return {"status": "ok", "record": res.get("result"), "message": "Đã thêm bản ghi DNS trên Cloudflare!"}


@router.put("/dns/{record_id}")
async def update_cf_dns(record_id: str, req: CFDnsRecordRequest, current_user: dict = Depends(get_current_user)):
    _, cfg, _ = await _resolve_domain_and_cf(req.domain_id, current_user)
    token, zone_id = _require_cf_connected(cfg)
    payload: Dict[str, Any] = {
        "type": req.type.upper().strip(),
        "name": req.name.strip(),
        "content": req.content.strip(),
        "ttl": int(req.ttl or 1),
        "proxied": bool(req.proxied) if req.type.upper() in ("A", "AAAA", "CNAME") else False
    }
    if req.type.upper() == "MX" and req.priority is not None:
        payload["priority"] = int(req.priority)
    res = _cf_request(token, "PUT", f"/zones/{zone_id}/dns_records/{record_id}", payload)
    return {"status": "ok", "record": res.get("result"), "message": "Đã cập nhật bản ghi DNS!"}


@router.delete("/dns/{record_id}")
async def delete_cf_dns(record_id: str, domain_id: int = Query(...), current_user: dict = Depends(get_current_user)):
    _, cfg, _ = await _resolve_domain_and_cf(domain_id, current_user)
    token, zone_id = _require_cf_connected(cfg)
    _cf_request(token, "DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
    return {"status": "ok", "message": "Đã xóa bản ghi DNS trên Cloudflare."}


@router.post("/cache/purge")
async def purge_cf_cache(req: CFPurgeCacheRequest, current_user: dict = Depends(get_current_user)):
    _, cfg, _ = await _resolve_domain_and_cf(req.domain_id, current_user)
    token, zone_id = _require_cf_connected(cfg)
    if req.purge_everything or not req.urls:
        payload = {"purge_everything": True}
    else:
        clean_urls = [u.strip() for u in req.urls if u.strip()][:30]
        payload = {"files": clean_urls}
    _cf_request(token, "POST", f"/zones/{zone_id}/purge_cache", payload)
    return {"status": "ok", "message": "Đã gửi lệnh xóa Cache lên Cloudflare thành công!"}


@router.post("/cache/dev-mode")
async def toggle_cf_dev_mode(req: CFDevModeRequest, current_user: dict = Depends(get_current_user)):
    _, cfg, _ = await _resolve_domain_and_cf(req.domain_id, current_user)
    token, zone_id = _require_cf_connected(cfg)
    val = "on" if req.enabled else "off"
    _cf_request(token, "PATCH", f"/zones/{zone_id}/settings/development_mode", {"value": val})
    return {"status": "ok", "enabled": req.enabled, "message": f"Development Mode đã {'bật (3 giờ)' if req.enabled else 'tắt'}."}


def _push_ip_to_cf(token: str, zone_id: str, ip: str, mode: str, note: str) -> Optional[str]:
    payload = {
        "mode": mode,
        "configuration": {"target": "ip", "value": ip.strip()},
        "notes": note[:500]
    }
    res = _cf_request(token, "POST", f"/zones/{zone_id}/firewall/access_rules/rules", payload)
    return (res.get("result") or {}).get("id")


async def _sync_fail2ban_for_domain(domain_id: int, cfg: Dict[str, Any]) -> Dict[str, Any]:
    token, zone_id = _require_cf_connected(cfg)
    from app.services.fail2ban_manager import get_fail2ban_status
    f2b = get_fail2ban_status()
    banned_ips: List[str] = []
    for j in (f2b.get("jails") or []):
        for ip in (j.get("banned_ips") or []):
            if ip not in banned_ips:
                banned_ips.append(ip)

    max_ips = max(1, int(cfg.get("f2b_max_ips") or 50))
    mode = cfg.get("f2b_ban_mode") or "block"

    # Lấy các rule hiện có để tránh đẩy trùng IP
    existing_res = _cf_request(token, "GET", f"/zones/{zone_id}/firewall/access_rules/rules?per_page=100")
    existing_ips = {
        (r.get("configuration") or {}).get("value")
        for r in (existing_res.get("result") or [])
    }

    pushed = 0
    errors = 0
    db = await get_db_connection()
    await _ensure_cf_tables(db)
    for ip in banned_ips[:max_ips]:
        if ip in existing_ips:
            continue
        try:
            rule_id = _push_ip_to_cf(token, zone_id, ip, mode, f"Fail2ban Auto-Sync ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
            await db.execute(
                "INSERT INTO cloudflare_blocked_ips (domain_id, ip, mode, cf_rule_id, source, note) VALUES (?, ?, ?, ?, 'fail2ban', 'Auto-sync từ Fail2ban')",
                (domain_id, ip, mode, rule_id or "")
            )
            pushed += 1
        except Exception:
            errors += 1

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await db.execute("UPDATE cloudflare_configs SET last_sync_at = ? WHERE domain_id = ?", (now_str, domain_id))
    await db.commit()
    await db.close()
    return {"total_f2b": len(banned_ips), "pushed": pushed, "errors": errors, "last_sync_at": now_str}


@router.get("/fail2ban")
async def get_cf_fail2ban_info(domain_id: int = Query(...), current_user: dict = Depends(get_current_user)):
    _, cfg, _ = await _resolve_domain_and_cf(domain_id, current_user)
    token, zone_id = _require_cf_connected(cfg)

    from app.services.fail2ban_manager import get_fail2ban_status
    f2b = get_fail2ban_status()

    rules_res = _cf_request(token, "GET", f"/zones/{zone_id}/firewall/access_rules/rules?per_page=50")
    cf_rules = rules_res.get("result") or []

    db = await get_db_connection()
    await _ensure_cf_tables(db)
    cur = await db.execute("SELECT * FROM cloudflare_blocked_ips WHERE domain_id = ? ORDER BY id DESC LIMIT 50", (domain_id,))
    history = [dict(r) for r in await cur.fetchall()]
    await db.close()

    return {
        "status": "ok",
        "fail2ban": f2b,
        "cf_rules": cf_rules,
        "history": history
    }


@router.post("/fail2ban/config")
async def save_cf_fail2ban_config(req: CFFail2banConfigRequest, current_user: dict = Depends(get_current_user)):
    await _resolve_domain_and_cf(req.domain_id, current_user)
    db = await get_db_connection()
    await _ensure_cf_tables(db)
    await db.execute("""
        UPDATE cloudflare_configs
        SET f2b_sync_enabled = ?, f2b_sync_interval = ?, f2b_ban_mode = ?, f2b_max_ips = ?, updated_at = CURRENT_TIMESTAMP
        WHERE domain_id = ?
    """, (1 if req.enabled else 0, max(30, req.interval_sec), req.ban_mode, max(1, req.max_ips), req.domain_id))
    await db.commit()
    await db.close()
    return {"status": "ok", "message": "Đã lưu cấu hình đồng bộ Fail2ban -> Cloudflare!"}


@router.post("/fail2ban/sync-now")
async def sync_cf_fail2ban_now(domain_id: int = Query(...), current_user: dict = Depends(get_current_user)):
    _, cfg, _ = await _resolve_domain_and_cf(domain_id, current_user)
    res = await _sync_fail2ban_for_domain(domain_id, cfg)
    return {"status": "ok", **res, "message": f"Đã đồng bộ xong: đẩy {res['pushed']} IP mới lên Cloudflare."}


@router.post("/fail2ban/block-ip")
async def block_ip_on_cf(req: CFBlockIpRequest, current_user: dict = Depends(get_current_user)):
    _, cfg, _ = await _resolve_domain_and_cf(req.domain_id, current_user)
    token, zone_id = _require_cf_connected(cfg)
    ip = req.ip.strip()
    if not re.match(r"^[0-9a-fA-F.:/]{3,45}$", ip):
        raise HTTPException(status_code=400, detail="Địa chỉ IP không hợp lệ")

    rule_id = _push_ip_to_cf(token, zone_id, ip, req.mode, req.note or "PressPanel Manual Block")
    db = await get_db_connection()
    await _ensure_cf_tables(db)
    await db.execute(
        "INSERT INTO cloudflare_blocked_ips (domain_id, ip, mode, cf_rule_id, source, note) VALUES (?, ?, ?, ?, 'manual', ?)",
        (req.domain_id, ip, req.mode, rule_id or "", req.note or "Chặn thủ công từ Panel")
    )
    await db.commit()
    await db.close()
    return {"status": "ok", "rule_id": rule_id, "message": f"Đã gửi IP {ip} ({req.mode}) lên Cloudflare thành công!"}


@router.delete("/fail2ban/rule/{rule_id}")
async def delete_cf_ip_rule(rule_id: str, domain_id: int = Query(...), current_user: dict = Depends(get_current_user)):
    _, cfg, _ = await _resolve_domain_and_cf(domain_id, current_user)
    token, zone_id = _require_cf_connected(cfg)
    _cf_request(token, "DELETE", f"/zones/{zone_id}/firewall/access_rules/rules/{rule_id}")
    db = await get_db_connection()
    await db.execute("DELETE FROM cloudflare_blocked_ips WHERE cf_rule_id = ? AND domain_id = ?", (rule_id, domain_id))
    await db.commit()
    await db.close()
    return {"status": "ok", "message": "Đã gỡ bỏ quy tắc chặn IP trên Cloudflare."}


@router.post("/under-attack")
async def toggle_cf_under_attack(req: CFUnderAttackRequest, current_user: dict = Depends(get_current_user)):
    _, cfg, _ = await _resolve_domain_and_cf(req.domain_id, current_user)
    token, zone_id = _require_cf_connected(cfg)
    target_level = "under_attack" if req.enabled else (req.fallback_level or "medium")
    _cf_request(token, "PATCH", f"/zones/{zone_id}/settings/security_level", {"value": target_level})
    return {
        "status": "ok",
        "security_level": target_level,
        "enabled": req.enabled,
        "message": "Đã BẬT chế độ I'm Under Attack! (Chống DDoS khẩn cấp)" if req.enabled else f"Đã TẮT Under Attack (Chuyển về mức {target_level})."
    }


@router.get("/settings")
async def get_cf_zone_settings(domain_id: int = Query(...), current_user: dict = Depends(get_current_user)):
    _, cfg, _ = await _resolve_domain_and_cf(domain_id, current_user)
    token, zone_id = _require_cf_connected(cfg)
    res = _cf_request(token, "GET", f"/zones/{zone_id}/settings")
    raw_items = res.get("result") or []
    wanted = {
        "ssl", "always_use_https", "security_level", "brotli",
        "browser_cache_ttl", "development_mode", "browser_check",
        "challenge_ttl", "hotlink_protection", "email_obfuscation"
    }
    parsed = {it["id"]: it["value"] for it in raw_items if it.get("id") in wanted}

    # Bot Fight Mode (nếu Token có quyền Bot Management)
    parsed["bot_fight_mode"] = False
    try:
        bot_res = _cf_request(token, "GET", f"/zones/{zone_id}/bot_management")
        bot_result = bot_res.get("result") or {}
        parsed["bot_fight_mode"] = bool(bot_result.get("fight_mode", False))
    except Exception:
        pass

    return {"status": "ok", "settings": parsed}


@router.post("/settings")
async def update_cf_zone_settings(req: CFZoneSettingsRequest, current_user: dict = Depends(get_current_user)):
    _, cfg, _ = await _resolve_domain_and_cf(req.domain_id, current_user)
    token, zone_id = _require_cf_connected(cfg)
    updates = []
    if req.ssl_mode:
        updates.append({"id": "ssl", "value": req.ssl_mode})
    if req.always_use_https:
        updates.append({"id": "always_use_https", "value": req.always_use_https})
    if req.security_level:
        updates.append({"id": "security_level", "value": req.security_level})
    if req.brotli:
        updates.append({"id": "brotli", "value": req.brotli})
    if req.browser_cache_ttl is not None:
        updates.append({"id": "browser_cache_ttl", "value": int(req.browser_cache_ttl)})
    if req.browser_check:
        updates.append({"id": "browser_check", "value": req.browser_check})
    if req.challenge_ttl is not None:
        updates.append({"id": "challenge_ttl", "value": int(req.challenge_ttl)})
    if req.hotlink_protection:
        updates.append({"id": "hotlink_protection", "value": req.hotlink_protection})
    if req.email_obfuscation:
        updates.append({"id": "email_obfuscation", "value": req.email_obfuscation})

    if updates:
        _cf_request(token, "PATCH", f"/zones/{zone_id}/settings", {"items": updates})

    bot_note = ""
    if req.bot_fight_mode is not None:
        try:
            _cf_request(token, "PUT", f"/zones/{zone_id}/bot_management", {"fight_mode": bool(req.bot_fight_mode)})
        except HTTPException as be:
            bot_note = f" (Lưu ý Bot Fight Mode chưa cập nhật được: {be.detail} — hãy kiểm tra quyền Bot Management:Edit trên API Token)"

    return {"status": "ok", "message": f"Đã cập nhật cấu hình bảo mật & tối ưu Cloudflare thành công!{bot_note}"}


async def start_cloudflare_fail2ban_sync_worker():
    """Tiến trình nền tự động đồng bộ IP bị Fail2ban chặn lên Cloudflare theo tần suất cấu hình"""
    while True:
        try:
            await asyncio.sleep(30)
            db = await get_db_connection()
            await _ensure_cf_tables(db)
            cur = await db.execute("SELECT * FROM cloudflare_configs WHERE f2b_sync_enabled = 1 AND zone_id != '' AND api_token != ''")
            rows = [dict(r) for r in await cur.fetchall()]
            await db.close()
            now_dt = datetime.now()
            for cfg in rows:
                interval = max(30, int(cfg.get("f2b_sync_interval") or 60))
                last_str = cfg.get("last_sync_at") or ""
                due = True
                if last_str:
                    try:
                        last_dt = datetime.strptime(last_str, "%Y-%m-%d %H:%M:%S")
                        if (now_dt - last_dt).total_seconds() < interval:
                            due = False
                    except Exception:
                        pass
                if due:
                    try:
                        await _sync_fail2ban_for_domain(cfg["domain_id"], cfg)
                    except Exception as se:
                        logger.debug(f"CF Fail2ban sync domain {cfg['domain_id']} skip: {se}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"CF Fail2ban worker error: {e}")
