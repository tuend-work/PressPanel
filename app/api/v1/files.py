import os
import shutil
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from typing import Optional
from app.api.deps import get_current_user

router = APIRouter(prefix="/files", tags=["File Manager"])

def resolve_target_path(user: dict, target_user: Optional[str], subpath: str) -> str:
    # 1. Nếu là admin, có thể xem thư mục của bất kỳ user nào trong /home hoặc thư mục web
    if user["role"] == "admin":
        if target_user:
            clean_u = "".join(c for c in target_user if c.isalnum() or c in ("_", "-"))
            base_dir = f"/home/{clean_u}"
        else:
            base_dir = "/home"
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
        # Khi thư mục thuộc sở hữu của web user linux khác mà user presspanel không có quyền r-x, dùng subprocess ls
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
        gatekeeper_app = "/opt/presspanel/bin/app"
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
        gatekeeper_app = "/opt/presspanel/bin/app"
        tmp_save_path = f"/tmp/presspanel_edit_{os.getpid()}_{os.path.basename(file_path)}.tmp"
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
        # Fallback quyền root nếu cha thư mục giới hạn quyền
        try:
            import subprocess
            if req.item_type == "folder":
                subprocess.run(["sudo", "mkdir", "-p", target_path], check=True)
            else:
                subprocess.run(["sudo", "touch", target_path], check=True)
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
        try:
            import subprocess
            subprocess.run(["sudo", "rm", "-rf", target_path], check=True)
            return {"status": "ok", "message": f"Đã xóa {os.path.basename(target_path)} thành công!"}
        except Exception as e:
            raise HTTPException(status_code=403, detail=f"Không có quyền xóa: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi xóa: {str(e)}")
