"""
PressPanel: telegram_notifier.py - Hệ thống Gửi Cảnh Báo Đa Kênh qua Telegram Bot
Hỗ trợ gửi thông báo tức thì khi máy chủ gặp sự cố quá tải CPU/RAM, dịch vụ dừng,
tấn công Brute-force hoặc sao lưu thất bại.
"""

import json
import logging
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional
from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

async def get_telegram_config() -> Dict[str, Any]:
    """Đọc cấu hình Telegram Bot từ database"""
    db = await get_db_connection()
    cursor = await db.execute("SELECT key, value FROM system_settings WHERE key LIKE 'telegram_%'")
    rows = await cursor.fetchall()
    cfg = {
        "bot_token": "",
        "chat_id": "",
        "enabled": False,
        "events": {
            "cpu_ram": True,       # Cảnh báo CPU hoặc RAM vượt 90%
            "service_down": True,  # Cảnh báo dịch vụ quan trọng dừng (MySQL, OLS, Nginx)
            "bruteforce": True,    # Cảnh báo IP dò mật khẩu SSH / Panel
            "backup": True         # Cảnh báo sao lưu thành công hoặc thất bại
        }
    }
    for k, v in rows:
        if k == "telegram_bot_token":
            cfg["bot_token"] = v or ""
        elif k == "telegram_chat_id":
            cfg["chat_id"] = v or ""
        elif k == "telegram_alerts_enabled":
            cfg["enabled"] = (v == "true")
        elif k == "telegram_events":
            try:
                cfg["events"].update(json.loads(v))
            except Exception:
                pass
    return cfg

def send_telegram_message_sync(bot_token: str, chat_id: str, text: str) -> bool:
    """Gửi tin nhắn Telegram qua HTTPS POST (sử dụng urllib chuẩn)"""
    if not bot_token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200
    except Exception as e:
        logger.error(f"Lỗi gửi tin nhắn Telegram: {e}")
        return False

async def send_telegram_alert(text: str, event_type: str = "alert") -> bool:
    """
    Gửi cảnh báo Telegram bất đồng bộ dựa trên chính sách sự kiện
    event_type: 'cpu_ram' | 'service_down' | 'bruteforce' | 'backup' | 'system'
    """
    cfg = await get_telegram_config()
    if not cfg["enabled"]:
        return False
    if not cfg["bot_token"] or not cfg["chat_id"]:
        return False

    # Kiểm tra xem sự kiện này có được bật trong cấu hình không
    if event_type in cfg["events"] and not cfg["events"][event_type]:
        return False

    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, send_telegram_message_sync, cfg["bot_token"], cfg["chat_id"], text)
