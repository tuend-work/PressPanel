"""
PressPanel: cloud_backup.py - Hệ thống Sao Lưu Đám Mây & Điểm Lưu Trữ Từ Xa
Hỗ trợ sao lưu toàn diện Website & Database nén .tar.gz, tự động đẩy lên:
AWS S3, Cloudflare R2, Wasabi, MinIO hoặc máy chủ SFTP từ xa kèm xoay vòng Retention.
"""

import os
import shutil
import tarfile
import logging
import datetime
import subprocess
import asyncio
from typing import Dict, Any, Optional, List
from app.core.config import settings
from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

BACKUP_LOCAL_DIR = getattr(settings, "BACKUP_DIR", "/opt/presspanel/data/backups")

def ensure_backup_dir():
    os.makedirs(BACKUP_LOCAL_DIR, exist_ok=True)
    try:
        for u in ["presspanel", "wpc"]:
            try:
                shutil.chown(BACKUP_LOCAL_DIR, user=u, group=u)
                break
            except Exception:
                pass
        os.chmod(BACKUP_LOCAL_DIR, 0o750)
    except Exception:
        pass

def upload_to_s3_sync(dest: Dict[str, Any], local_file: str, remote_key: str) -> bool:
    """Tải tệp lên S3-compatible (AWS S3, Wasabi, Cloudflare R2, MinIO)"""
    # 1. Thử dùng AWS CLI nếu có sẵn
    endpoint_arg = f"--endpoint-url {dest['endpoint']}" if dest.get("endpoint") else ""
    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = dest.get("access_key", "")
    env["AWS_SECRET_ACCESS_KEY"] = dest.get("secret_key", "")
    env["AWS_DEFAULT_REGION"] = dest.get("region", "us-east-1")

    bucket = dest.get("bucket", "").strip()
    s3_uri = f"s3://{bucket}/{remote_key}"

    if shutil.which("aws"):
        cmd = ["aws", "s3", "cp", local_file, s3_uri]
        if dest.get("endpoint"):
            cmd.extend(["--endpoint-url", str(dest["endpoint"]).strip()])
        res = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        return res.returncode == 0

    # 2. Thử dùng thư viện python boto3 nếu đã cài
    try:
        import boto3
        from botocore.config import Config
        cfg = Config(signature_version="s3v4") if dest.get("endpoint") else None
        s3 = boto3.client(
            "s3",
            endpoint_url=dest.get("endpoint") or None,
            aws_access_key_id=dest.get("access_key"),
            aws_secret_access_key=dest.get("secret_key"),
            region_name=dest.get("region", "us-east-1"),
            config=cfg
        )
        s3.upload_file(local_file, bucket, remote_key)
        return True
    except Exception as e:
        logger.warning(f"Boto3 upload thất bại: {e}")

    # 3. Fallback: Dùng rclone nếu có
    if shutil.which("rclone"):
        rclone_remote = (
            f":s3,provider=Other,endpoint={dest.get('endpoint') or ''},"
            f"access_key_id={dest.get('access_key') or ''},"
            f"secret_access_key={dest.get('secret_key') or ''}:{bucket}/{remote_key}"
        )
        res = subprocess.run(["rclone", "copyto", local_file, rclone_remote], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        return res.returncode == 0

    logger.error("Không tìm thấy aws cli, boto3 hoặc rclone để tải lên S3!")
    return False

def upload_to_sftp_sync(dest: Dict[str, Any], local_file: str, remote_filename: str) -> bool:
    """Tải tệp lên máy chủ SFTP từ xa bằng scp/sftp"""
    host = dest.get("sftp_host", "").strip()
    port = str(dest.get("sftp_port", 22))
    user = dest.get("sftp_user", "").strip()
    path = dest.get("sftp_path", "/backups").rstrip("/")
    remote_target = f"{user}@{host}:{path}/{remote_filename}"

    # Dùng sshpass + scp nếu có mật khẩu (truyền mật khẩu qua env SSHPASS tránh lộ trên argv)
    if shutil.which("sshpass") and dest.get("sftp_pass"):
        env = os.environ.copy()
        env["SSHPASS"] = str(dest["sftp_pass"])
        cmd = ["sshpass", "-e", "scp", "-P", port, "-o", "StrictHostKeyChecking=no", local_file, remote_target]
        res = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        return res.returncode == 0
    elif shutil.which("scp"):
        cmd = ["scp", "-P", port, "-o", "StrictHostKeyChecking=no", local_file, remote_target]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        return res.returncode == 0

    return False

async def create_website_backup(domain_id: int, user_id: int, destination_id: int = 0) -> Dict[str, Any]:
    """
    Tạo bản sao lưu toàn diện cho 1 Website (Mã nguồn + Database MySQL)
    Lưu cục bộ và tự động đẩy lên Cloud Destination nếu có chỉ định destination_id > 0
    """
    ensure_backup_dir()
    db = await get_db_connection()
    c = await db.execute("""
        SELECT d.domain_name, d.document_root, u.username
        FROM domains d
        JOIN users u ON d.user_id = u.id
        WHERE d.id = ?
    """, (domain_id,))
    row = await c.fetchone()
    if not row:
        return {"status": "error", "message": "Không tìm thấy tên miền!"}

    domain_name = row[0]
    doc_root = row[1]
    username = row[2]

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"backup_{domain_name}_{timestamp}.tar.gz"
    local_archive = os.path.join(BACKUP_LOCAL_DIR, backup_filename)

    # 1. Tạo bản ghi backup với trạng thái 'running'
    cursor = await db.execute("""
        INSERT INTO backups (user_id, domain_id, backup_type, storage_type, file_path, status, destination_id)
        VALUES (?, ?, 'full', 'local', ?, 'running', ?)
    """, (user_id, domain_id, local_archive, destination_id))
    backup_id = cursor.lastrowid
    await db.commit()

    # 2. Đóng gói mã nguồn và xuất Database MySQL sang thư mục tạm
    tmp_backup_dir = f"/tmp/wpc_bk_{domain_name}_{timestamp}"
    os.makedirs(tmp_backup_dir, exist_ok=True)
    sql_dump_file = os.path.join(tmp_backup_dir, f"{domain_name}_database.sql")

    try:
        # Tìm DB liên kết với WordPress nếu có
        c_wp = await db.execute("SELECT db_name FROM wp_instances WHERE domain_id = ?", (domain_id,))
        wp_db = await c_wp.fetchone()
        if wp_db and wp_db[0]:
            db_name = wp_db[0]
            with open(sql_dump_file, "wb") as sf:
                subprocess.run(["mysqldump", db_name], stdout=sf, stderr=subprocess.DEVNULL, timeout=60, check=False)

        # Đóng gói tệp tin bằng tar (không dùng shell=True)
        res = subprocess.run(
            ["tar", "-czf", local_archive, "-C", doc_root, ".", "-C", tmp_backup_dir, "."],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600
        )
        if res.returncode != 0:
            raise Exception("Lỗi khi nén tệp tin mã nguồn bằng tar")

        file_size = os.path.getsize(local_archive) if os.path.exists(local_archive) else 0

        # 3. Đẩy lên Cloud Destination nếu được chọn
        storage_type = "local"
        if destination_id > 0:
            c_dest = await db.execute("SELECT name, storage_type, endpoint, bucket, access_key, secret_key, region, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_path FROM backup_destinations WHERE id = ?", (destination_id,))
            dest_row = await c_dest.fetchone()
            if dest_row:
                dest_dict = {
                    "name": dest_row[0], "storage_type": dest_row[1], "endpoint": dest_row[2],
                    "bucket": dest_row[3], "access_key": dest_row[4], "secret_key": dest_row[5],
                    "region": dest_row[6], "sftp_host": dest_row[7], "sftp_port": dest_row[8],
                    "sftp_user": dest_row[9], "sftp_pass": dest_row[10], "sftp_path": dest_row[11]
                }
                storage_type = dest_dict["storage_type"]
                loop = asyncio.get_event_loop()
                uploaded = False
                if storage_type == "s3":
                    remote_key = f"websites/{domain_name}/{backup_filename}"
                    uploaded = await loop.run_in_executor(None, upload_to_s3_sync, dest_dict, local_archive, remote_key)
                elif storage_type == "sftp":
                    uploaded = await loop.run_in_executor(None, upload_to_sftp_sync, dest_dict, local_archive, backup_filename)

                if not uploaded:
                    logger.warning(f"Không thể đẩy bản sao lưu lên Cloud {dest_dict['name']}, giữ bản lưu cục bộ.")

        # 4. Cập nhật trạng thái thành công
        await db.execute("""
            UPDATE backups
            SET status = 'success', file_size_bytes = ?, storage_type = ?
            WHERE id = ?
        """, (file_size, storage_type, backup_id))
        await db.commit()

        # 5. Gửi cảnh báo Telegram
        from app.services.telegram_notifier import send_telegram_alert
        size_mb = round(file_size / (1024 * 1024), 2)
        asyncio.create_task(send_telegram_alert(
            f"📦 <b>[Sao Lưu Hoàn Tất]</b>\n\n"
            f"🌐 <b>Website:</b> <code>{domain_name}</code>\n"
            f"💾 <b>Dung lượng:</b> <code>{size_mb} MB</code>\n"
            f"☁️ <b>Lưu trữ:</b> <code>{storage_type.upper()}</code>\n"
            f"⚡ Tệp tin: <code>{backup_filename}</code>",
            event_type="backup"
        ))

        return {
            "status": "ok",
            "message": f"Đã tạo bản sao lưu thành công ({size_mb} MB) cho {domain_name}!",
            "file_name": backup_filename,
            "size_bytes": file_size,
            "storage_type": storage_type
        }

    except Exception as e:
        logger.error(f"Lỗi tạo bản sao lưu cho {domain_name}: {e}")
        await db.execute("UPDATE backups SET status = 'failed', error_msg = ? WHERE id = ?", (str(e), backup_id))
        await db.commit()
        return {"status": "error", "message": f"Sao lưu thất bại: {str(e)}"}
    finally:
        shutil.rmtree(tmp_backup_dir, ignore_errors=True)
