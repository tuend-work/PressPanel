"""
PressPanel: ipset_manager.py - Hệ thống Quản Lý IP Blocklists Danh Sách Đen Diện Rộng
Sử dụng công nghệ Linux Kernel ipset (Atomic Swap) để chặn hàng trăm nghìn IP độc hại
với độ phức tạp O(1), không tiêu tốn CPU máy chủ.
"""

import os
import re
import shutil
import logging
import subprocess
import urllib.request
import asyncio
from typing import Dict, Any, List
from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

IPSET_NAME = "presspanel-block4"
SWAP_NAME = "presspanel-swap"

def is_valid_ip_or_cidr(line: str) -> bool:
    """Kiểm tra chuỗi có phải IPv4 hoặc CIDR hợp lệ không"""
    ip_pattern = r"^([0-9]{1,3}\.){3}[0-9]{1,3}(\/([0-9]|[1-2][0-9]|3[0-2]))?$"
    return bool(re.match(ip_pattern, line))

def ensure_ipset_iptables_chain():
    """Đảm bảo bảng ipset tồn tại và được móc vào iptables INPUT chain"""
    if not shutil.which("ipset") or not shutil.which("iptables"):
        return False
    try:
        # 1. Tạo ipset nếu chưa có
        subprocess.run(
            ["ipset", "create", IPSET_NAME, "hash:net", "family", "inet", "hashsize", "4096", "maxelem", "1000000", "-exist"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
        )
        # 2. Kiểm tra quy tắc iptables
        res = subprocess.run(
            ["iptables", "-C", "INPUT", "-m", "set", "--match-set", IPSET_NAME, "src", "-j", "DROP"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
        )
        if res.returncode != 0:
            # Chèn lên đầu bảng INPUT để chặn sớm nhất
            subprocess.run(
                ["iptables", "-I", "INPUT", "1", "-m", "set", "--match-set", IPSET_NAME, "src", "-j", "DROP"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
            )
        return True
    except Exception as e:
        logger.error(f"Lỗi khởi tạo ipset/iptables: {e}")
        return False

def get_ipset_stats() -> Dict[str, Any]:
    """Lấy số lượng IP đang được chặn trong kernel ipset"""
    total_entries = 0
    active = False
    if shutil.which("ipset"):
        try:
            res = subprocess.run(
                ["ipset", "list", IPSET_NAME, "-terse"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
            )
            if res.returncode == 0:
                active = True
                m = re.search(r"Number of entries:\s+(\d+)", res.stdout)
                if m:
                    total_entries = int(m.group(1))
        except Exception:
            pass
    return {
        "installed": bool(shutil.which("ipset")),
        "active": active,
        "set_name": IPSET_NAME,
        "total_blocked_ips": total_entries
    }

def sync_all_blocklists_sync() -> Dict[str, Any]:
    """
    Tải toàn bộ danh sách đen từ các URL được kích hoạt và nạp nguyên tử (Atomic Swap) vào ipset
    """
    if not shutil.which("ipset"):
        return {"status": "error", "message": "Lệnh ipset chưa được cài đặt trên máy chủ (cần 'dnf install ipset' hoặc 'apt install ipset')"}

    ensure_ipset_iptables_chain()

    import sqlite3
    from app.core.config import settings

    all_ips = set()
    url_counts = {}

    try:
        conn = sqlite3.connect(settings.DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id, name, url FROM firewall_blocklists WHERE enabled = 1")
        sources = c.fetchall()

        for s_id, s_name, s_url in sources:
            count = 0
            try:
                req = urllib.request.Request(s_url, headers={"User-Agent": "PressPanel-Firewall-Sync/1.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    lines = resp.read().decode("utf-8", errors="ignore").splitlines()
                    for line in lines:
                        line = line.strip()
                        if not line or line.startswith("#") or line.startswith(";"):
                            continue
                        # Bóc tách IP đầu tiên nếu dòng có kèm ghi chú
                        parts = line.split()
                        candidate = parts[0]
                        if is_valid_ip_or_cidr(candidate):
                            all_ips.add(candidate)
                            count += 1
                c.execute("UPDATE firewall_blocklists SET ip_count = ?, last_sync = CURRENT_TIMESTAMP WHERE id = ?", (count, s_id))
                url_counts[s_name] = count
            except Exception as e:
                logger.warning(f"Lỗi tải blocklist từ {s_url}: {e}")
                url_counts[s_name] = f"Lỗi: {str(e)}"
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Lỗi kết nối CSDL khi đồng bộ blocklists: {e}")

    if not all_ips:
        return {"status": "ok", "message": "Không có IP nào được nạp (các nguồn blocklist trống hoặc không có nguồn nào bật)", "total_ips": 0}

    # 3. Tạo bảng tạm SWAP_NAME và nạp toàn bộ IP vào đó
    try:
        subprocess.run(["ipset", "destroy", SWAP_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(
            ["ipset", "create", SWAP_NAME, "hash:net", "family", "inet", "hashsize", "4096", "maxelem", "1000000", "-exist"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        # Ghi các lệnh add vào tiến trình `ipset restore` để nạp siêu tốc hàng triệu dòng
        restore_lines = [f"add {SWAP_NAME} {ip} -exist" for ip in all_ips]
        restore_payload = "\n".join(restore_lines) + "\n"

        proc = subprocess.Popen(["ipset", "restore"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = proc.communicate(input=restore_payload)

        # 4. Atomic Swap: Hoán đổi bảng tạm thành bảng chính tức thời không rớt mạng
        subprocess.run(["ipset", "swap", IPSET_NAME, SWAP_NAME], check=True)
        subprocess.run(["ipset", "destroy", SWAP_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        logger.info(f"Đã nạp thành công {len(all_ips)} IP vào ipset {IPSET_NAME} qua cơ chế Atomic Swap!")
        return {
            "status": "ok",
            "message": f"Đã đồng bộ và nạp thành công {len(all_ips):,} địa chỉ IP vào nhân Linux ipset!",
            "total_ips": len(all_ips),
            "sources": url_counts
        }
    except Exception as e:
        logger.error(f"Lỗi trong quá trình nạp ipset: {e}")
        return {"status": "error", "message": f"Lỗi khi nạp vào ipset: {str(e)}"}

async def sync_all_blocklists() -> Dict[str, Any]:
    """Chạy đồng bộ blocklists trên threadpool bất đồng bộ"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, sync_all_blocklists_sync)
