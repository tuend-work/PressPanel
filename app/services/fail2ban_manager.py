"""
WPCloud: fail2ban_manager.py - Hệ thống Quản Lý Fail2ban Brute-Force Shield
Tương tác với fail2ban-client để theo dõi các cuộc tấn công dò mật khẩu SSH/Panel
và tự động khóa IP kẻ tấn công.
"""

import shutil
import logging
import subprocess
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

def get_fail2ban_status() -> Dict[str, Any]:
    """Lấy trạng thái tổng thể của Fail2ban và danh sách các Jail đang kích hoạt"""
    installed = bool(shutil.which("fail2ban-client"))
    running = False
    jails = []

    if installed:
        try:
            res = subprocess.run(
                ["fail2ban-client", "status"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
            )
            if res.returncode == 0:
                running = True
                for line in res.stdout.splitlines():
                    if "Jail list:" in line:
                        parts = line.split("Jail list:")[1].strip()
                        jails = [j.strip() for j in parts.split(",") if j.strip()]
        except Exception as e:
            logger.warning(f"Lỗi kiểm tra trạng thái fail2ban: {e}")

    jail_details = []
    total_banned = 0
    if running and jails:
        for j in jails:
            try:
                r = subprocess.run(
                    ["fail2ban-client", "status", j],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
                )
                banned_ips = []
                failed_count = 0
                if r.returncode == 0:
                    for l in r.stdout.splitlines():
                        if "Currently failed:" in l:
                            try:
                                failed_count = int(l.split(":")[1].strip())
                            except Exception:
                                pass
                        if "Banned IP list:" in l:
                            ips = l.split("Banned IP list:")[1].strip().split()
                            banned_ips = [ip.strip() for ip in ips if ip.strip()]
                total_banned += len(banned_ips)
                jail_details.append({
                    "jail": j,
                    "currently_failed": failed_count,
                    "banned_count": len(banned_ips),
                    "banned_ips": banned_ips
                })
            except Exception:
                pass

    return {
        "installed": installed,
        "running": running,
        "total_jails": len(jails),
        "total_banned": total_banned,
        "jails": jail_details
    }

def unban_ip(jail: str, ip: str) -> Dict[str, Any]:
    """Gỡ bỏ lệnh chặn (Unban) một địa chỉ IP khỏi Fail2ban"""
    if not shutil.which("fail2ban-client"):
        return {"status": "error", "message": "Fail2ban chưa được cài đặt"}
    try:
        res = subprocess.run(
            ["fail2ban-client", "set", jail, "unbanip", ip],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
        )
        if res.returncode == 0:
            return {"status": "ok", "message": f"Đã mở khóa IP {ip} khỏi Jail {jail} thành công!"}
        else:
            return {"status": "error", "message": f"Lỗi từ Fail2ban: {res.stderr.strip() or res.stdout.strip()}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def toggle_fail2ban_service(action: str) -> Dict[str, Any]:
    """Khởi động, dừng hoặc restart Fail2ban service"""
    if action not in ("start", "stop", "restart", "enable"):
        return {"status": "error", "message": "Hành động không hợp lệ"}
    try:
        subprocess.run(["systemctl", action, "fail2ban"], check=True, timeout=10)
        return {"status": "ok", "message": f"Đã thực hiện {action} dịch vụ Fail2ban thành công!"}
    except Exception as e:
        return {"status": "error", "message": f"Lỗi systemctl: {str(e)}"}
