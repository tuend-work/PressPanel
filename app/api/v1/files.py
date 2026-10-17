import os
import shutil
import re
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from typing import Optional
from app.api.deps import get_current_user

router = APIRouter(prefix="/files", tags=["File Manager"])

import sqlite3

def is_domain_owner(user_id: int, target_user: str) -> bool:
    try:
        db_path = "/opt/wpc/data/panel.db"
        if not os.path.exists(db_path):
            base_p = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            db_path = os.path.join(base_p, "data", "panel.db")
        if not os.path.exists(db_path):
            return False
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT id FROM domains WHERE user_id = ? AND (document_root LIKE ? OR domain_name LIKE ?)",
                    (user_id, f"%/home/{target_user}/%", f"%{target_user}%"))
        row = cur.fetchone()
        conn.close()
        return bool(row)
    except Exception:
        return False

def resolve_target_path(user: dict, target_user: Optional[str], subpath: str) -> str:
    clean_u = "".join(c for c in target_user if c.isalnum() or c in ("_", "-")) if target_user else None

    # 1. Nếu là admin, có thể xem thư mục của bất kỳ user nào trong /home hoặc thư mục web
    if user["role"] == "admin":
        if clean_u:
            base_dir = f"/home/{clean_u}"
        else:
            base_dir = "/home"
    elif clean_u and clean_u != user["username"]:
        # Cho phép Hosting User xem và quản lý file của website thuộc quyền sở hữu của mình
        if is_domain_owner(user["id"], clean_u):
            base_dir = f"/home/{clean_u}"
        else:
            base_dir = f"/home/{user['username']}"
    else:
        # User thường chỉ được xem trong phạm vi /home/<username>
        base_dir = f"/home/{user['username']}"

    clean_sub = subpath.lstrip("/\\")
    real_base = os.path.realpath(base_dir)
    real_target = os.path.realpath(os.path.join(real_base, clean_sub))

    # Chống Path Traversal vượt khỏi base_dir
    if not real_target.startswith(real_base) and real_target != real_base:
        raise HTTPException(status_code=403, detail="Phát hiện hành vi Path Traversal không an toàn!")
    return real_target

class FileContentUpdate(BaseModel):
    path: str
    content: str
    target_user: Optional[str] = None

class CreateItemRequest(BaseModel):
    path: str
    item_type: str  # "file" hoặc "folder"
    target_user: Optional[str] = None

class DeleteItemRequest(BaseModel):
    path: str
    target_user: Optional[str] = None

@router.get("/list")
async def list_files(
    path: str = Query("", description="Đường dẫn tương đối"),
    target_user: Optional[str] = Query(None, description="Username mục tiêu (dành cho Admin)"),
    current_user: dict = Depends(get_current_user)
):
    target_dir = resolve_target_path(current_user, target_user, path)
    if not os.path.exists(target_dir):
        # Tạo thư mục tự động nếu là /home/<user>
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception:
            return {"items": [], "current_path": path, "absolute_path": target_dir}

    items = []
    try:
        with os.scandir(target_dir) as entries:
            for entry in entries:
                try:
                    info = entry.stat()
                    items.append({
                        "name": entry.name,
                        "is_dir": entry.is_dir(),
                        "size": info.st_size if entry.is_file() else 0,
                        "modified": int(info.st_mtime),
                        "mode": oct(info.st_mode)[-3:]
                    })
                except Exception:
                    continue
    except PermissionError:
        # Khi thư mục thuộc sở hữu của web user linux khác mà user wpc không có quyền r-x, dùng subprocess ls
        try:
            import subprocess
            proc = subprocess.run(
                ["ls", "-la", "--time-style=+%s", target_dir],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if proc.returncode != 0:
                # Thử với sudo ls
                proc = subprocess.run(
                    ["sudo", "ls", "-la", "--time-style=+%s", target_dir],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
            if proc.returncode == 0:
                lines = proc.stdout.strip().split("\n")
                for line in lines[1:]:  # Bỏ qua dòng 'total ...'
                    parts = line.split()
                    if len(parts) >= 7:
                        mode_str = parts[0]
                        is_directory = mode_str.startswith("d")
                        try:
                            size = int(parts[4])
                        except ValueError:
                            size = 0
                        try:
                            mtime = int(parts[5])
                        except ValueError:
                            mtime = 0
                        name = " ".join(parts[6:])
                        if name in (".", ".."):
                            continue
                        items.append({
                            "name": name,
                            "is_dir": is_directory,
                            "size": size,
                            "modified": mtime,
                            "mode": "755" if is_directory else "644"
                        })
        except Exception:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập thư mục này trên máy chủ.")

    sorted_items = sorted(items, key=lambda x: (not x["is_dir"], x["name"].lower()))
    return {
        "items": sorted_items,
        "current_path": path,
        "absolute_path": target_dir,
        "target_user": target_user or current_user["username"]
    }

@router.get("/content")
async def get_file_content(
    path: str = Query(..., description="Đường dẫn file"),
    target_user: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    file_path = resolve_target_path(current_user, target_user, path)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp tin.")

    # Thử đọc trực tiếp
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return {"path": path, "content": f.read(), "absolute_path": file_path}
    except PermissionError:
        # Fallback đọc qua gatekeeper SUID
        gatekeeper_app = "/opt/wpc/bin/app"
        if os.path.isfile(gatekeeper_app):
            import subprocess
            proc = subprocess.run([gatekeeper_app, "config-read", file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode == 0:
                return {"path": path, "content": proc.stdout, "absolute_path": file_path}
        # Fallback sudo cat
        try:
            import subprocess
            proc = subprocess.run(["sudo", "cat", file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode == 0:
                return {"path": path, "content": proc.stdout, "absolute_path": file_path}
        except Exception:
            pass
        raise HTTPException(status_code=403, detail="Không có quyền đọc tệp tin này.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể đọc file: {str(e)}")

@router.put("/content")
async def save_file_content(
    req: FileContentUpdate,
    current_user: dict = Depends(get_current_user)
):
    file_path = resolve_target_path(current_user, req.target_user, req.path)
    
    # 1. Thử ghi trực tiếp
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"status": "ok", "message": "Đã lưu nội dung tệp tin thành công!"}
    except PermissionError:
        # 2. Nếu gặp lỗi Permission Denied do thuộc sở hữu web user Linux, dùng SUID Gatekeeper
        gatekeeper_app = "/opt/wpc/bin/app"
        tmp_save_path = f"/tmp/wpc_edit_{os.getpid()}_{os.path.basename(file_path)}.tmp"
        try:
            with open(tmp_save_path, "w", encoding="utf-8") as ftmp:
                ftmp.write(req.content)

            import subprocess
            if os.path.isfile(gatekeeper_app):
                proc = subprocess.run([gatekeeper_app, "config-write", tmp_save_path, file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode == 0:
                    return {"status": "ok", "message": "Đã lưu nội dung tệp tin thành công!"}

            # Fallback sudo cp
            proc_sudo = subprocess.run(["sudo", "cp", tmp_save_path, file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if os.path.exists(tmp_save_path):
                os.remove(tmp_save_path)
            if proc_sudo.returncode == 0:
                return {"status": "ok", "message": "Đã lưu nội dung tệp tin thành công!"}
            raise HTTPException(status_code=403, detail="Không có quyền ghi tệp tin này.")
        except Exception as e:
            if os.path.exists(tmp_save_path):
                os.remove(tmp_save_path)
            raise HTTPException(status_code=500, detail=f"Không thể lưu tệp: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể lưu tệp: {str(e)}")

@router.post("/create")
async def create_file_or_dir(
    req: CreateItemRequest,
    current_user: dict = Depends(get_current_user)
):
    target_path = resolve_target_path(current_user, req.target_user, req.path)
    try:
        if req.item_type == "folder":
            os.makedirs(target_path, exist_ok=True)
            return {"status": "ok", "message": f"Tạo thư mục {os.path.basename(target_path)} thành công!"}
        else:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write("")
            return {"status": "ok", "message": f"Tạo tệp tin {os.path.basename(target_path)} thành công!"}
    except PermissionError:
        # Fallback quyền root qua Gatekeeper SUID (tránh sudo password prompt)
        gatekeeper_app = "/opt/wpc/bin/app"
        try:
            import subprocess
            if os.path.isfile(gatekeeper_app):
                op = "file-mkdir" if req.item_type == "folder" else "file-touch"
                proc = subprocess.run([gatekeeper_app, op, target_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode == 0:
                    return {"status": "ok", "message": f"Tạo {os.path.basename(target_path)} thành công!"}

            # Fallback nếu chưa biên dịch gatekeeper
            if req.item_type == "folder":
                subprocess.run(["mkdir", "-p", target_path], check=True)
            else:
                subprocess.run(["touch", target_path], check=True)
            return {"status": "ok", "message": f"Tạo {os.path.basename(target_path)} thành công!"}
        except Exception as e:
            raise HTTPException(status_code=403, detail=f"Không có quyền khởi tạo: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khởi tạo: {str(e)}")

@router.delete("/delete")
async def delete_file_or_dir(
    req: DeleteItemRequest,
    current_user: dict = Depends(get_current_user)
):
    target_path = resolve_target_path(current_user, req.target_user, req.path)
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Mục cần xóa không tồn tại.")

    try:
        if os.path.isdir(target_path):
            shutil.rmtree(target_path)
        else:
            os.remove(target_path)
        return {"status": "ok", "message": f"Đã xóa {os.path.basename(target_path)} thành công!"}
    except PermissionError:
        gatekeeper_app = "/opt/wpc/bin/app"
        try:
            import subprocess
            if os.path.isfile(gatekeeper_app):
                proc = subprocess.run([gatekeeper_app, "file-rm", target_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode == 0:
                    return {"status": "ok", "message": f"Đã xóa {os.path.basename(target_path)} thành công!"}

            subprocess.run(["rm", "-rf", target_path], check=True)
            return {"status": "ok", "message": f"Đã xóa {os.path.basename(target_path)} thành công!"}
        except Exception as e:
            raise HTTPException(status_code=403, detail=f"Không có quyền xóa: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi xóa: {str(e)}")

class ArchiveRequest(BaseModel):
    current_path: str = ""
    archive_name: str
    format: str = "zip"  # zip | tar | tar.gz
    items: list[str]
    target_user: Optional[str] = None

@router.post("/compress")
async def compress_files(
    req: ArchiveRequest,
    current_user: dict = Depends(get_current_user)
):
    if not req.items:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một tệp hoặc thư mục để nén.")

    work_dir = resolve_target_path(current_user, req.target_user, req.current_path)
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=404, detail="Thư mục làm việc không tồn tại.")

    # Đảm bảo tên file nén có đuôi mở rộng phù hợp
    archive_name = req.archive_name.strip()
    if req.format == "zip" and not archive_name.endswith(".zip"):
        archive_name += ".zip"
    elif req.format == "tar" and not archive_name.endswith(".tar"):
        archive_name += ".tar"
    elif req.format in ("tar.gz", "tgz", "gz") and not (archive_name.endswith(".tar.gz") or archive_name.endswith(".tgz")):
        archive_name += ".tar.gz"

    archive_full_path = os.path.join(work_dir, archive_name)

    # 1. Thử dùng zipfile hoặc tarfile thuần của Python
    try:
        if req.format == "zip":
            import zipfile
            with zipfile.ZipFile(archive_full_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for item_name in req.items:
                    item_full = os.path.join(work_dir, item_name)
                    if os.path.isdir(item_full):
                        for root, _, files in os.walk(item_full):
                            for f in files:
                                f_path = os.path.join(root, f)
                                arcname = os.path.relpath(f_path, work_dir)
                                zf.write(f_path, arcname)
                    elif os.path.isfile(item_full):
                        zf.write(item_full, item_name)
            return {"status": "ok", "message": f"Nén thành công tệp {archive_name}!", "archive_name": archive_name}
        elif req.format in ("tar", "tar.gz", "tgz", "gz"):
            import tarfile
            mode = "w:gz" if req.format in ("tar.gz", "tgz", "gz") else "w"
            with tarfile.open(archive_full_path, mode) as tf:
                for item_name in req.items:
                    item_full = os.path.join(work_dir, item_name)
                    if os.path.exists(item_full):
                        tf.add(item_full, arcname=item_name)
            return {"status": "ok", "message": f"Nén thành công tệp {archive_name}!", "archive_name": archive_name}
    except PermissionError:
        # 2. Nếu thiếu quyền ghi hoặc đọc, gọi qua Gatekeeper SUID
        gatekeeper_app = "/opt/wpc/bin/app"
        clean_items = " ".join([f"'{item}'" for item in req.items if not re.search(r"[';`$&|]", item)])
        try:
            import subprocess
            if os.path.isfile(gatekeeper_app):
                proc = subprocess.run(
                    [gatekeeper_app, "file-archive", work_dir, archive_name, req.format, clean_items],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                if proc.returncode == 0:
                    return {"status": "ok", "message": f"Nén thành công tệp {archive_name}!", "archive_name": archive_name}
            raise HTTPException(status_code=500, detail="Không thể thực hiện nén tệp tin qua Gatekeeper.")
        except Exception as ge:
            raise HTTPException(status_code=500, detail=f"Lỗi nén tệp: {str(ge)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi nén tệp: {str(e)}")
