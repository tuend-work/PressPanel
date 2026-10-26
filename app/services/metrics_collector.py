import os
import re
import time
import asyncio
import platform
import subprocess
from typing import List, Tuple, Any
from app.db.session import get_db_connection
from app.api.v1.system import get_system_stats

async def prune_old_metrics(retention_days: int = 7):
    """
    Xóa các bản ghi metrics cũ hơn số ngày retention_days quy định.
    Hỗ trợ 3, 7, 14, 30, 60, 90 ngày.
    """
    if retention_days not in (3, 7, 14, 30, 60, 90):
        retention_days = 7
    try:
        db = await get_db_connection()
        await db.execute(f"DELETE FROM resource_metrics WHERE created_at < datetime('now', '-{retention_days} days')")
        await db.commit()
        await db.close()
    except Exception as e:
        print(f"[-] Lỗi dọn dẹp metrics cũ: {e}")

async def start_metrics_collector():
    """
    Vòng lặp chạy ngầm thu thập metrics của Server, Users, Domains mỗi 60 giây (1 phút)
    và lưu trực tiếp vào bảng SQLite `resource_metrics` nếu tính năng được bật.
    """
    print("[+] Khởi động Background Metrics Collector (1 snapshot/phút)...")
    await asyncio.sleep(5)  # Chờ hệ thống khởi tạo hoàn tất

    cleanup_counter = 0

    while True:
        try:
            # 1. Đọc cấu hình từ system_settings
            db = await get_db_connection()
            c_cfg = await db.execute("SELECT key, value FROM system_settings WHERE key IN ('metrics_history_enabled', 'metrics_retention_days')")
            rows = await c_cfg.fetchall()
            settings_map = {r["key"]: r["value"] for r in rows}

            enabled = settings_map.get("metrics_history_enabled", "true").lower() in ("true", "1", "yes")
            try:
                retention_days = int(settings_map.get("metrics_retention_days", "7"))
            except ValueError:
                retention_days = 7
            if retention_days not in (3, 7, 14, 30, 60, 90):
                retention_days = 7

            if not enabled:
                await db.close()
                await asyncio.sleep(60)
                continue

            batch_records: List[Tuple[Any, ...]] = []

            # 2. Thu thập chỉ số của Toàn bộ Máy Chủ (Server)
            sys_stats = get_system_stats()
            srv_cpu = round(float(sys_stats["cpu"]["percent"]), 1)
            srv_ram = int(sys_stats["ram"]["used_gb"] * 1024)
            srv_disk = int(sys_stats["disk"]["used_gb"] * 1024)
            srv_bw_in = round(float(sys_stats["network"]["down_speed_kb"]), 1)
            srv_bw_out = round(float(sys_stats["network"]["up_speed_kb"]), 1)
            srv_pps_in = int(sys_stats["network"]["in_pps"])
            srv_pps_out = int(sys_stats["network"]["out_pps"])

            batch_records.append((
                "server", 0, srv_cpu, srv_ram, srv_disk,
                srv_bw_in, srv_bw_out, srv_pps_in, srv_pps_out
            ))

            # 3. Thu thập chỉ số của từng Website (Domains)
            c_dom = await db.execute("SELECT id, domain_name, user_id, document_root FROM domains WHERE is_system = 0")
            domains_list = await c_dom.fetchall()

            for d in domains_list:
                dom_id = d["id"]
                dom_name = d["domain_name"] or ""
                doc_root = d["document_root"] or ""
                clean_d = re.sub(r"^(https?://)?(www\.)?", "", dom_name.strip().lower()).rstrip("/")
                clean_u = re.sub(r"[^a-z0-9]", "", clean_d)[:30]

                d_cpu = 0.0
                d_ram_mb = 0
                d_disk_mb = 0

                if platform.system() == "Linux":
                    # Quét CPU và RAM từ ps
                    try:
                        p = subprocess.run(
                            ["ps", "-u", clean_u, "-o", "pid,%cpu,rss", "--no-headers"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0
                        )
                        if p.returncode == 0 and p.stdout:
                            for l in p.stdout.splitlines():
                                parts = l.strip().split()
                                if len(parts) >= 3:
                                    try:
                                        d_cpu += float(parts[1])
                                        d_ram_mb += int(int(parts[2]) / 1024)
                                    except ValueError:
                                        pass
                    except Exception:
                        pass

                    # Fallback quét pool FPM
                    if d_cpu == 0.0 and d_ram_mb == 0:
                        try:
                            pg = subprocess.run(
                                ["pgrep", "-f", f"pool {clean_u}"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=0.8
                            )
                            if pg.returncode == 0 and pg.stdout:
                                pids = [pid.strip() for pid in pg.stdout.splitlines() if pid.strip()]
                                if pids:
                                    ps_fpm = subprocess.run(
                                        ["ps", "-p", ",".join(pids[:20]), "-o", "pid,%cpu,rss", "--no-headers"],
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=0.8
                                    )
                                    if ps_fpm.returncode == 0 and ps_fpm.stdout:
                                        for l in ps_fpm.stdout.splitlines():
                                            parts = l.strip().split()
                                            if len(parts) >= 3:
                                                try:
                                                    d_cpu += float(parts[1])
                                                    d_ram_mb += int(int(parts[2]) / 1024)
                                                except ValueError:
                                                    pass
                        except Exception:
                            pass

                    # Đo dung lượng thư mục web
                    if doc_root and os.path.exists(doc_root):
                        try:
                            du = subprocess.run(["du", "-sm", doc_root], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0)
                            if du.returncode == 0 and du.stdout:
                                d_disk_mb = int(du.stdout.split()[0])
                        except Exception:
                            pass

                batch_records.append((
                    "domain", dom_id, round(d_cpu, 1), d_ram_mb, d_disk_mb,
                    0.0, 0.0, 0, 0
                ))

            # 4. Thu thập chỉ số của từng User
            c_usr = await db.execute("SELECT id, username FROM users WHERE role != 'admin'")
            users_list = await c_usr.fetchall()

            for u in users_list:
                uid = u["id"]
                uname = u["username"]
                u_cpu = 0.0
                u_ram_mb = 0
                u_disk_mb = 0

                if platform.system() == "Linux":
                    try:
                        p = subprocess.run(
                            ["ps", "-u", uname, "-o", "pid,%cpu,rss", "--no-headers"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0
                        )
                        if p.returncode == 0 and p.stdout:
                            for l in p.stdout.splitlines():
                                parts = l.strip().split()
                                if len(parts) >= 3:
                                    try:
                                        u_cpu += float(parts[1])
                                        u_ram_mb += int(int(parts[2]) / 1024)
                                    except ValueError:
                                        pass
                    except Exception:
                        pass

                    home_path = f"/home/{uname}"
                    if os.path.exists(home_path):
                        try:
                            du = subprocess.run(["du", "-sm", home_path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0)
                            if du.returncode == 0 and du.stdout:
                                u_disk_mb = int(du.stdout.split()[0])
                        except Exception:
                            pass

                batch_records.append((
                    "user", uid, round(u_cpu, 1), u_ram_mb, u_disk_mb,
                    0.0, 0.0, 0, 0
                ))

            # 5. Lưu vào database SQLite trong 1 transaction nhanh
            if batch_records:
                await db.executemany("""
                    INSERT INTO resource_metrics (
                        target_type, target_id, cpu_pct, mem_mb, disk_mb,
                        bw_in_kb, bw_out_kb, pps_in, pps_out
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch_records)
                await db.commit()

            # 6. Dọn dẹp bản ghi cũ định kỳ mỗi 60 phút (60 vòng lặp)
            cleanup_counter += 1
            if cleanup_counter >= 60:
                cleanup_counter = 0
                await db.execute(f"DELETE FROM resource_metrics WHERE created_at < datetime('now', '-{retention_days} days')")
                await db.commit()

            await db.close()

        except Exception as e:
            print(f"[-] Lỗi trong chu kỳ thu thập metrics: {e}")

        # Chờ 60 giây (1 phút) cho chu kỳ tiếp theo
        await asyncio.sleep(60)
