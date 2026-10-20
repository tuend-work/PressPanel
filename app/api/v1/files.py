import os
import shutil
import re
import json
import time
import subprocess
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from pathlib import Path

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
        cur.execute("""
            SELECT d.id FROM domains d
            WHERE (d.user_id = ? OR d.user_id IN (SELECT id FROM users WHERE parent_id = ?))
              AND (d.document_root = ? OR d.document_root LIKE ?)
        """, (user_id, user_id, f"/home/{target_user}", f"/home/{target_user}/%"))
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
        if is_domain_owner(user["id"], clean_u):
            base_dir = f"/home/{clean_u}"
        else:
            base_dir = f"/home/{user['username']}"
    else:
        base_dir = f"/home/{user['username']}"

    clean_sub = subpath.lstrip("/\\")
    real_base = os.path.realpath(base_dir)
    real_target = os.path.realpath(os.path.join(real_base, clean_sub))

    # Chống Path Traversal vượt khỏi base_dir
    try:
        Path(real_target).relative_to(Path(real_base))
    except ValueError:
        raise HTTPException(status_code=403, detail="Phát hiện hành vi Path Traversal không an toàn!")
    return real_target

def sync_owner(path: str):
    """Đảm bảo file/thư mục mới tạo hoặc chỉnh sửa có owner:group chuẩn xác với user sở hữu thư mục /home/<user>"""
    try:
        m = re.match(r"^/home/([^/]+)", path)
        if m:
            u = m.group(1)
            subprocess.run(["sudo", "chown", "-R", f"{u}:{u}", path], check=False)
        else:
            parent = os.path.dirname(path) if not os.path.isdir(path) else os.path.dirname(path.rstrip("/\\"))
            if os.path.exists(parent):
                st = os.stat(parent)
                subprocess.run(["sudo", "chown", "-R", f"{st.st_uid}:{st.st_gid}", path], check=False)
    except Exception:
        pass

def get_recycle_bin_dir(user: dict, target_user: Optional[str]) -> str:
    u = target_user if (user["role"] == "admin" and target_user) else user["username"]
    r_dir = f"/home/{u}/.recycle_bin"
    os.makedirs(r_dir, exist_ok=True)
    return r_dir

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

class BatchDeleteRequest(BaseModel):
    current_path: str = ""
    items: List[str]
    use_recycle_bin: bool = True
    target_user: Optional[str] = None

class RenameRequest(BaseModel):
    current_path: str = ""
    old_name: str
    new_name: str
    target_user: Optional[str] = None

class ChmodRequest(BaseModel):
    current_path: str = ""
    name: str
    mode: str  # vd: "755", "644"
    target_user: Optional[str] = None

class RemoteDownloadRequest(BaseModel):
    current_path: str = ""
    url: str
    filename: Optional[str] = None
    target_user: Optional[str] = None

class PasteRequest(BaseModel):
    action: str  # "copy" hoặc "cut"
    src_path: str
    dest_path: str
    items: List[str]
    target_user: Optional[str] = None

class ArchiveRequest(BaseModel):
    current_path: str = ""
    archive_name: str
    format: str = "zip"  # zip | tar | tar.gz
    items: List[str]
    target_user: Optional[str] = None

class CalculateSizeRequest(BaseModel):
    current_path: str = ""
    name: str
    target_user: Optional[str] = None

class SearchContentRequest(BaseModel):
    current_path: str = ""
    query: str
    target_user: Optional[str] = None
    case_sensitive: bool = False

# ==============================================================================
# Endpoints: List, Content & File Operations
# ==============================================================================

@router.get("/list")
async def list_files(
    path: str = Query("", description="Đường dẫn tương đối"),
    target_user: Optional[str] = Query(None, description="Username mục tiêu (dành cho Admin)"),
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm"),
    include_subdir: bool = Query(False, description="Tìm đệ quy thư mục con"),
    current_user: dict = Depends(get_current_user)
):
    target_dir = resolve_target_path(current_user, target_user, path)
    if not os.path.exists(target_dir):
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception:
            return {"items": [], "current_path": path, "absolute_path": target_dir}

    # Nếu tìm kiếm có bao gồm thư mục con
    if search and include_subdir:
        q = search.lower().strip()
        matched_items = []
        max_results = 200
        count = 0
        skip_dirs = {".git", ".recycle_bin", "node_modules", "vendor", ".cache"}
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            rel_dir = os.path.relpath(root, target_dir)
            if rel_dir == ".":
                rel_dir = ""

            for d in dirs:
                if q in d.lower():
                    item_rel = os.path.join(rel_dir, d).replace("\\", "/")
                    full_p = os.path.join(root, d)
                    try:
                        st = os.stat(full_p)
                        mode_oct = oct(st.st_mode)[-3:]
                        mtime = int(st.st_mtime)
                    except Exception:
                        mode_oct = "755"
                        mtime = int(time.time())
                    matched_items.append({
                        "name": item_rel,
                        "is_dir": True,
                        "is_symlink": os.path.islink(full_p),
                        "link_target": "",
                        "size": 0,
                        "size_formatted": None,
                        "modified": mtime,
                        "mode": mode_oct,
                        "owner": "root",
                        "pmsn_owner": f"{mode_oct} / root",
                        "protected": False
                    })
                    count += 1
                    if count >= max_results:
                        break

            for f in files:
                if q in f.lower():
                    item_rel = os.path.join(rel_dir, f).replace("\\", "/")
                    full_p = os.path.join(root, f)
                    try:
                        st = os.stat(full_p)
                        sz = st.st_size
                        mode_oct = oct(st.st_mode)[-3:]
                        mtime = int(st.st_mtime)
                    except Exception:
                        sz = 0
                        mode_oct = "644"
                        mtime = int(time.time())
                    matched_items.append({
                        "name": item_rel,
                        "is_dir": False,
                        "is_symlink": os.path.islink(full_p),
                        "link_target": "",
                        "size": sz,
                        "size_formatted": format_bytes(sz),
                        "modified": mtime,
                        "mode": mode_oct,
                        "owner": "root",
                        "pmsn_owner": f"{mode_oct} / root",
                        "protected": False
                    })
                    count += 1
                    if count >= max_results:
                        break
            if count >= max_results:
                break

        sorted_items = sorted(matched_items, key=lambda x: (not x["is_dir"], x["name"].lower()))
        return {
            "items": sorted_items,
            "current_path": path,
            "absolute_path": target_dir,
            "target_user": target_user or current_user["username"],
            "is_search": True
        }

    items = []
    # 1. Thử duyệt thư mục bằng Python os.scandir
    try:
        with os.scandir(target_dir) as entries:
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                    is_symlink = entry.is_symlink()
                    link_target = ""
                    if is_symlink:
                        try:
                            link_target = os.readlink(entry.path)
                        except Exception:
                            pass

                    # QUAN TRỌNG: Nếu là symlink nhưng trỏ tới thư mục -> COI LÀ FOLDER
                    real_path = os.path.realpath(entry.path)
                    is_directory = os.path.isdir(real_path)

                    # Lấy thông tin owner
                    owner_name = "root"
                    try:
                        import pwd
                        owner_name = pwd.getpwuid(info.st_uid).pw_name
                    except Exception:
                        pass

                    mode_oct = oct(info.st_mode)[-3:]

                    items.append({
                        "name": entry.name,
                        "is_dir": is_directory,
                        "is_symlink": is_symlink,
                        "link_target": link_target,
                        "size": info.st_size if not is_directory else 0,
                        "size_formatted": None if is_directory else format_bytes(info.st_size),
                        "modified": int(info.st_mtime),
                        "mode": mode_oct,
                        "owner": owner_name,
                        "pmsn_owner": f"{mode_oct} / {owner_name}",
                        "protected": False
                    })
                except Exception:
                    continue
    except PermissionError:
        # Fallback bằng ls -la khi không có quyền đọc trực tiếp
        try:
            proc = subprocess.run(
                ["sudo", "ls", "-la", "--time-style=+%s", target_dir],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if proc.returncode == 0:
                lines = proc.stdout.strip().split("\n")
                for line in lines[1:]:
                    parts = line.split()
                    if len(parts) >= 7:
                        mode_str = parts[0]
                        owner = parts[2] if len(parts) > 2 else "root"
                        raw_name = " ".join(parts[6:])
                        if raw_name in (".", ".."):
                            continue

                        is_symlink = mode_str.startswith("l")
                        clean_name = raw_name
                        link_target = ""
                        is_directory = mode_str.startswith("d")

                        if " -> " in raw_name:
                            clean_name, link_target = raw_name.split(" -> ", 1)
                            clean_name = clean_name.strip()
                            link_target = link_target.strip()
                            full_target = link_target if link_target.startswith("/") else os.path.join(target_dir, link_target)
                            if os.path.isdir(full_target) or os.path.isdir(os.path.realpath(full_target)):
                                is_directory = True

                        try:
                            size = int(parts[4])
                        except ValueError:
                            size = 0
                        try:
                            mtime = int(parts[5])
                        except ValueError:
                            mtime = 0

                        mode_code = "755" if is_directory else "644"
                        items.append({
                            "name": clean_name,
                            "is_dir": is_directory,
                            "is_symlink": is_symlink,
                            "link_target": link_target,
                            "size": size if not is_directory else 0,
                            "size_formatted": None if is_directory else format_bytes(size),
                            "modified": mtime,
                            "mode": mode_code,
                            "owner": owner,
                            "pmsn_owner": f"{mode_code} / {owner}",
                            "protected": False
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

def format_bytes(b: int) -> str:
    if b < 1024:
        return f"{b}.00 B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.2f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.2f} MB"
    else:
        return f"{b / (1024 * 1024 * 1024):.2f} GB"

@router.get("/content")
async def get_file_content(
    path: str = Query(..., description="Đường dẫn file"),
    target_user: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    file_path = resolve_target_path(current_user, target_user, path)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp tin.")

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return {"path": path, "content": f.read(), "absolute_path": file_path}
    except PermissionError:
        gatekeeper_app = "/opt/wpc/bin/app"
        if os.path.isfile(gatekeeper_app):
            proc = subprocess.run([gatekeeper_app, "config-read", file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode == 0:
                return {"path": path, "content": proc.stdout, "absolute_path": file_path}
        try:
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
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        sync_owner(file_path)
        return {"status": "ok", "message": "Đã lưu nội dung tệp tin thành công!"}
    except PermissionError:
        gatekeeper_app = "/opt/wpc/bin/app"
        tmp_save_path = f"/tmp/wpc_edit_{os.getpid()}_{os.path.basename(file_path)}.tmp"
        try:
            with open(tmp_save_path, "w", encoding="utf-8") as ftmp:
                ftmp.write(req.content)
            if os.path.isfile(gatekeeper_app):
                proc = subprocess.run([gatekeeper_app, "config-write", tmp_save_path, file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode == 0:
                    sync_owner(file_path)
                    return {"status": "ok", "message": "Đã lưu nội dung tệp tin thành công!"}
            proc_sudo = subprocess.run(["sudo", "cp", tmp_save_path, file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if os.path.exists(tmp_save_path):
                os.remove(tmp_save_path)
            if proc_sudo.returncode == 0:
                sync_owner(file_path)
                return {"status": "ok", "message": "Đã lưu nội dung tệp tin thành công!"}
            raise HTTPException(status_code=403, detail="Không có quyền ghi tệp tin này.")
        except Exception as e:
            if os.path.exists(tmp_save_path):
                os.remove(tmp_save_path)
            raise HTTPException(status_code=500, detail=f"Không thể lưu tệp: {str(e)}")

@router.post("/create")
async def create_file_or_dir(
    req: CreateItemRequest,
    current_user: dict = Depends(get_current_user)
):
    target_path = resolve_target_path(current_user, req.target_user, req.path)
    parent_dir = os.path.dirname(target_path)
    try:
        if req.item_type == "folder":
            try:
                os.makedirs(target_path, exist_ok=True)
                os.chmod(target_path, 0o755)
            except PermissionError:
                proc = subprocess.run(["sudo", "mkdir", "-p", target_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode != 0:
                    raise HTTPException(status_code=500, detail=f"Lỗi khởi tạo thư mục: {proc.stderr}")
                subprocess.run(["sudo", "chmod", "755", target_path], check=False)

            sync_owner(target_path)
            return {"status": "ok", "message": f"Tạo thư mục {os.path.basename(target_path)} thành công!"}
        else:
            created = False
            try:
                os.makedirs(parent_dir, exist_ok=True)
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write("")
                os.chmod(target_path, 0o644)
                created = True
            except PermissionError:
                # Fallback bằng sudo touch / sudo cp khi tiến trình API wpc không có quyền ghi trực tiếp vào thư mục web user
                subprocess.run(["sudo", "mkdir", "-p", parent_dir], check=False)
                proc = subprocess.run(["sudo", "touch", target_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode == 0:
                    subprocess.run(["sudo", "chmod", "644", target_path], check=False)
                    created = True
                else:
                    tmp_file = f"/tmp/wpc_new_{os.getpid()}_{os.path.basename(target_path)}.tmp"
                    try:
                        with open(tmp_file, "w", encoding="utf-8") as ftmp:
                            ftmp.write("")
                        proc_cp = subprocess.run(["sudo", "cp", tmp_file, target_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                        if os.path.exists(tmp_file):
                            os.remove(tmp_file)
                        if proc_cp.returncode == 0:
                            subprocess.run(["sudo", "chmod", "644", target_path], check=False)
                            created = True
                    except Exception:
                        if os.path.exists(tmp_file):
                            os.remove(tmp_file)

            if not created:
                raise HTTPException(status_code=403, detail="Không có quyền tạo tệp tin trong thư mục này.")

            sync_owner(target_path)
            return {"status": "ok", "message": f"Tạo tệp tin {os.path.basename(target_path)} thành công!"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khởi tạo: {str(e)}")

@router.post("/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    current_path: str = Query(""),
    target_user: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    work_dir = resolve_target_path(current_user, target_user, current_path)
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=404, detail="Thư mục làm việc không tồn tại.")

    saved_files = []
    for f in files:
        safe_fn = os.path.basename(f.filename)
        dest_path = os.path.join(work_dir, safe_fn)
        try:
            try:
                with open(dest_path, "wb") as buffer:
                    shutil.copyfileobj(f.file, buffer)
                os.chmod(dest_path, 0o644)
            except PermissionError:
                tmp_dest = f"/tmp/wpc_upload_{os.getpid()}_{safe_fn}"
                with open(tmp_dest, "wb") as buffer:
                    shutil.copyfileobj(f.file, buffer)
                subprocess.run(["sudo", "cp", tmp_dest, dest_path], check=True)
                subprocess.run(["sudo", "chmod", "644", dest_path], check=False)
                if os.path.exists(tmp_dest):
                    os.remove(tmp_dest)

            sync_owner(dest_path)
            saved_files.append(safe_fn)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lỗi tải lên {safe_fn}: {str(e)}")

    return {"status": "ok", "message": f"Đã tải lên thành công {len(saved_files)} tệp tin!", "files": saved_files}

@router.post("/calculate-size")
async def calculate_size(
    req: CalculateSizeRequest,
    current_user: dict = Depends(get_current_user)
):
    work_dir = resolve_target_path(current_user, req.target_user, req.current_path)
    target_path = os.path.join(work_dir, req.name)
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Không tìm thấy mục chỉ định.")

    total_bytes = 0
    if os.path.isfile(target_path):
        total_bytes = os.path.getsize(target_path)
    else:
        try:
            # Ưu tiên dùng du -sb trên Linux
            proc = subprocess.run(["du", "-sb", target_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
            if proc.returncode == 0:
                total_bytes = int(proc.stdout.split()[0])
            else:
                for root, _, fls in os.walk(target_path):
                    for fl in fls:
                        fp = os.path.join(root, fl)
                        if not os.path.islink(fp):
                            total_bytes += os.path.getsize(fp)
        except Exception:
            for root, _, fls in os.walk(target_path):
                for fl in fls:
                    try:
                        fp = os.path.join(root, fl)
                        if not os.path.islink(fp):
                            total_bytes += os.path.getsize(fp)
                    except Exception:
                        pass

    return {
        "status": "ok",
        "name": req.name,
        "bytes": total_bytes,
        "formatted": format_bytes(total_bytes)
    }

@router.post("/batch-delete")
async def batch_delete_files(
    req: BatchDeleteRequest,
    current_user: dict = Depends(get_current_user)
):
    work_dir = resolve_target_path(current_user, req.target_user, req.current_path)
    if not req.items:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một mục để xóa.")

    recycle_dir = get_recycle_bin_dir(current_user, req.target_user) if req.use_recycle_bin else ""
    meta_file = os.path.join(recycle_dir, "metadata.json") if req.use_recycle_bin else ""

    metadata = []
    if req.use_recycle_bin and os.path.isfile(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as mf:
                metadata = json.load(mf)
        except Exception:
            metadata = []

    deleted_count = 0
    for item_name in req.items:
        src = os.path.join(work_dir, item_name)
        if not os.path.exists(src):
            continue

        if req.use_recycle_bin:
            ts = int(time.time() * 1000)
            recycle_name = f"{ts}_{item_name}"
            dest = os.path.join(recycle_dir, recycle_name)
            try:
                shutil.move(src, dest)
                metadata.append({
                    "id": str(ts),
                    "original_name": item_name,
                    "recycle_name": recycle_name,
                    "original_path": src,
                    "deleted_at": int(time.time()),
                    "is_dir": os.path.isdir(dest)
                })
                deleted_count += 1
            except Exception:
                subprocess.run(["sudo", "mv", src, dest], check=False)
                deleted_count += 1
        else:
            try:
                if os.path.isdir(src):
                    shutil.rmtree(src)
                else:
                    os.remove(src)
                deleted_count += 1
            except Exception:
                subprocess.run(["sudo", "rm", "-rf", src], check=False)
                deleted_count += 1

    if req.use_recycle_bin and metadata:
        try:
            with open(meta_file, "w", encoding="utf-8") as mf:
                json.dump(metadata, mf, indent=2)
        except Exception:
            pass

    action_text = "chuyển vào Thùng rác" if req.use_recycle_bin else "xóa vĩnh viễn"
    return {"status": "ok", "message": f"Đã {action_text} {deleted_count} mục thành công!", "count": deleted_count}

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
    except Exception:
        subprocess.run(["sudo", "rm", "-rf", target_path], check=False)
        return {"status": "ok", "message": f"Đã xóa {os.path.basename(target_path)} thành công!"}

@router.get("/recycle-bin")
async def get_recycle_bin(
    target_user: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    recycle_dir = get_recycle_bin_dir(current_user, target_user)
    meta_file = os.path.join(recycle_dir, "metadata.json")
    if not os.path.isfile(meta_file):
        return {"status": "ok", "items": [], "count": 0}

    try:
        with open(meta_file, "r", encoding="utf-8") as mf:
            items = json.load(mf)
        return {"status": "ok", "items": items, "count": len(items)}
    except Exception:
        return {"status": "ok", "items": [], "count": 0}

@router.post("/recycle-bin/restore")
async def restore_from_recycle_bin(
    recycle_name: str = Query(...),
    target_user: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    recycle_dir = get_recycle_bin_dir(current_user, target_user)
    meta_file = os.path.join(recycle_dir, "metadata.json")
    if not os.path.isfile(meta_file):
        raise HTTPException(status_code=404, detail="Thùng rác trống.")

    with open(meta_file, "r", encoding="utf-8") as mf:
        metadata = json.load(mf)

    item = next((m for m in metadata if m.get("recycle_name") == recycle_name), None)
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy mục trong thùng rác.")

    src = os.path.join(recycle_dir, recycle_name)
    dest = item["original_path"]
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    try:
        shutil.move(src, dest)
    except Exception:
        subprocess.run(["sudo", "mv", src, dest], check=False)

    new_meta = [m for m in metadata if m.get("recycle_name") != recycle_name]
    with open(meta_file, "w", encoding="utf-8") as mf:
        json.dump(new_meta, mf, indent=2)

    return {"status": "ok", "message": f"Đã khôi phục {item['original_name']} thành công!"}

@router.post("/recycle-bin/empty")
async def empty_recycle_bin(
    target_user: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    recycle_dir = get_recycle_bin_dir(current_user, target_user)
    try:
        for entry in os.scandir(recycle_dir):
            try:
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path)
                else:
                    os.remove(entry.path)
            except Exception:
                pass
    except Exception:
        subprocess.run(["sudo", "rm", "-rf", f"{recycle_dir}/*"], check=False)

    return {"status": "ok", "message": "Đã dọn sạch thùng rác hoàn toàn!"}

@router.post("/rename")
async def rename_item(
    req: RenameRequest,
    current_user: dict = Depends(get_current_user)
):
    work_dir = resolve_target_path(current_user, req.target_user, req.current_path)
    old_p = os.path.join(work_dir, req.old_name)
    new_p = os.path.join(work_dir, req.new_name)

    if not os.path.exists(old_p):
        raise HTTPException(status_code=404, detail="Tệp tin nguồn không tồn tại.")
    if os.path.exists(new_p):
        raise HTTPException(status_code=400, detail="Tên mới đã tồn tại trên thư mục.")

    try:
        try:
            os.rename(old_p, new_p)
        except PermissionError:
            proc = subprocess.run(["sudo", "mv", old_p, new_p], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode != 0:
                raise Exception(proc.stderr or "sudo mv failed")

        sync_owner(new_p)
        return {"status": "ok", "message": f"Đổi tên thành công sang {req.new_name}!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi đổi tên: {str(e)}")

@router.post("/chmod")
async def chmod_item(
    req: ChmodRequest,
    current_user: dict = Depends(get_current_user)
):
    work_dir = resolve_target_path(current_user, req.target_user, req.current_path)
    target = os.path.join(work_dir, req.name)
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="Mục chỉ định không tồn tại.")

    try:
        mode_val = int(req.mode, 8)
        try:
            os.chmod(target, mode_val)
        except PermissionError:
            proc = subprocess.run(["sudo", "chmod", req.mode, target], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode != 0:
                raise Exception(proc.stderr or "sudo chmod failed")

        return {"status": "ok", "message": f"Phân quyền {req.name} thành {req.mode} thành công!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi phân quyền: {str(e)}")

@router.post("/remote-download")
async def remote_download(
    req: RemoteDownloadRequest,
    current_user: dict = Depends(get_current_user)
):
    work_dir = resolve_target_path(current_user, req.target_user, req.current_path)
    url = req.url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="URL không hợp lệ (phải bắt đầu bằng http:// hoặc https://)")

    fn = req.filename or os.path.basename(url.split("?")[0]) or "downloaded_file"
    dest = os.path.join(work_dir, fn)

    try:
        proc = subprocess.run(["curl", "-fsSL", "--connect-timeout", "15", url, "-o", dest], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if proc.returncode != 0:
            raise HTTPException(status_code=400, detail="Không thể tải tệp từ URL chỉ định.")
        sync_owner(dest)
        return {"status": "ok", "message": f"Tải tệp {fn} từ xa thành công!", "filename": fn}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi tải file từ xa: {str(e)}")

@router.post("/paste")
async def paste_items(
    req: PasteRequest,
    current_user: dict = Depends(get_current_user)
):
    src_work_dir = resolve_target_path(current_user, req.target_user, req.src_path)
    dest_work_dir = resolve_target_path(current_user, req.target_user, req.dest_path)

    pasted = 0
    for item in req.items:
        src = os.path.join(src_work_dir, item)
        dest = os.path.join(dest_work_dir, item)
        if not os.path.exists(src):
            continue

        try:
            if req.action == "cut":
                try:
                    shutil.move(src, dest)
                except PermissionError:
                    subprocess.run(["sudo", "mv", src, dest], check=True)
            else:
                try:
                    if os.path.isdir(src):
                        shutil.copytree(src, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src, dest)
                except PermissionError:
                    if os.path.isdir(src):
                        subprocess.run(["sudo", "cp", "-r", src, dest], check=True)
                    else:
                        subprocess.run(["sudo", "cp", src, dest], check=True)

            sync_owner(dest)
            pasted += 1
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lỗi dán tệp {item}: {str(e)}")

    return {"status": "ok", "message": f"Đã dán thành công {pasted} mục!", "count": pasted}

@router.get("/download")
async def download_file(
    path: str = Query(..., description="Đường dẫn file"),
    target_user: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    file_path = resolve_target_path(current_user, target_user, path)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp tin.")

    return FileResponse(file_path, filename=os.path.basename(file_path))

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

    archive_name = req.archive_name.strip()
    if req.format == "zip" and not archive_name.endswith(".zip"):
        archive_name += ".zip"
    elif req.format == "tar" and not archive_name.endswith(".tar"):
        archive_name += ".tar"
    elif req.format in ("tar.gz", "tgz", "gz") and not (archive_name.endswith(".tar.gz") or archive_name.endswith(".tgz")):
        archive_name += ".tar.gz"

    archive_full_path = os.path.join(work_dir, archive_name)
    target_dest = f"/tmp/wpc_zip_{os.getpid()}_{archive_name}"

    try:
        if req.format == "zip":
            import zipfile
            with zipfile.ZipFile(target_dest, "w", zipfile.ZIP_DEFLATED) as zf:
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
        elif req.format in ("tar", "tar.gz", "tgz", "gz"):
            import tarfile
            mode = "w:gz" if req.format in ("tar.gz", "tgz", "gz") else "w"
            with tarfile.open(target_dest, mode) as tf:
                for item_name in req.items:
                    item_full = os.path.join(work_dir, item_name)
                    if os.path.exists(item_full):
                        tf.add(item_full, arcname=item_name)

        # Chuyển từ /tmp sang work_dir và phân quyền
        try:
            shutil.move(target_dest, archive_full_path)
            os.chmod(archive_full_path, 0o644)
        except PermissionError:
            subprocess.run(["sudo", "mv", target_dest, archive_full_path], check=True)
            subprocess.run(["sudo", "chmod", "644", archive_full_path], check=False)

        sync_owner(archive_full_path)
        return {"status": "ok", "message": f"Nén thành công tệp {archive_name}!", "archive_name": archive_name}
    except Exception as e:
        if os.path.exists(target_dest):
            os.remove(target_dest)
        raise HTTPException(status_code=500, detail=f"Lỗi nén tệp: {str(e)}")

@router.post("/search-content")
async def search_file_content(
    req: SearchContentRequest,
    current_user: dict = Depends(get_current_user)
):
    work_dir = resolve_target_path(current_user, req.target_user, req.current_path)
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=404, detail="Thư mục làm việc không tồn tại.")

    query = req.query.strip()
    if not query:
        return {"items": [], "count": 0}

    results = []
    max_results = 80

    # 1. Thử dùng grep trên hệ điều hành Linux
    try:
        cmd = ["grep", "-rnI"]
        if not req.case_sensitive:
            cmd.append("-i")
        cmd.extend([
            "--exclude-dir=.git",
            "--exclude-dir=.recycle_bin",
            "--exclude-dir=node_modules",
            "--exclude-dir=vendor",
            "--exclude-dir=.cache",
            "-m", "5",
            query,
            "."
        ])
        proc = subprocess.run(cmd, cwd=work_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        if proc.returncode in (0, 1) and proc.stdout:
            lines = proc.stdout.strip().split("\n")
            for line in lines:
                if not line.strip():
                    continue
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    fpath = parts[0].lstrip("./")
                    try:
                        lineno = int(parts[1])
                    except ValueError:
                        lineno = 1
                    snippet = parts[2].strip()
                    if len(snippet) > 200:
                        snippet = snippet[:200] + "..."
                    results.append({
                        "file": fpath,
                        "line": lineno,
                        "snippet": snippet
                    })
                    if len(results) >= max_results:
                        break
            if results:
                return {"items": results, "count": len(results)}
    except Exception:
        pass

    # 2. Fallback bằng Python os.walk (hoạt động tốt trên mọi môi trường)
    count = 0
    skip_dirs = {".git", ".recycle_bin", "node_modules", "vendor", ".cache"}
    flags = 0 if req.case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(query), flags)

    for root, dirs, files in os.walk(work_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in (".zip", ".gz", ".tar", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".exe", ".bin", ".woff", ".woff2", ".ttf"):
                continue
            fpath = os.path.join(root, f)
            try:
                if os.path.getsize(fpath) > 2 * 1024 * 1024:
                    continue
                rel_fpath = os.path.relpath(fpath, work_dir).replace("\\", "/")
                with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                    for idx, line in enumerate(fh, 1):
                        if pattern.search(line):
                            snip = line.strip()
                            if len(snip) > 200:
                                snip = snip[:200] + "..."
                            results.append({
                                "file": rel_fpath,
                                "line": idx,
                                "snippet": snip
                            })
                            count += 1
                            if count >= max_results:
                                break
            except Exception:
                continue
            if count >= max_results:
                break
        if count >= max_results:
            break

    return {"items": results, "count": len(results)}
