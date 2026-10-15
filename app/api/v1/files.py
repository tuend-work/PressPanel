import os
import shutil
import zipfile
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from app.api.deps import get_current_user

router = APIRouter(prefix="/files", tags=["File Manager"])

def get_safe_user_path(username: str, subpath: str) -> str:
    user_root = os.path.realpath(f"/home/{username}")
    target_path = os.path.realpath(os.path.join(user_root, subpath.lstrip("/")))
    if not target_path.startswith(user_root):
        raise HTTPException(status_code=403, detail="Phat hien hanh vi Path Traversal nguy hiem!")
    return target_path

class FileContentUpdate(BaseModel):
    path: str
    content: str

@router.get("/list")
async def list_files(path: str = Query("", description="Duong dan tuong doi trong thu muc user"), current_user: dict = Depends(get_current_user)):
    target_dir = get_safe_user_path(current_user["username"], path)
    if not os.path.exists(target_dir):
        return []

    items = []
    try:
        with os.scandir(target_dir) as entries:
            for entry in entries:
                info = entry.stat()
                items.append({
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": info.st_size if entry.is_file() else 0,
                    "modified": info.st_mtime,
                    "mode": oct(info.st_mode)[-3:]
                })
    except PermissionError:
        raise HTTPException(status_code=403, detail="Khong co quyen truy cap thu muc nay")
    return sorted(items, key=lambda x: (not x["is_dir"], x["name"].lower()))

@router.get("/content")
async def get_file_content(path: str, current_user: dict = Depends(get_current_user)):
    file_path = get_safe_user_path(current_user["username"], path)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Khong tim thay tep tin")
    
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return {"path": path, "content": f.read()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Khong the doc tep: {str(e)}")

@router.put("/content")
async def save_file_content(req: FileContentUpdate, current_user: dict = Depends(get_current_user)):
    file_path = get_safe_user_path(current_user["username"], req.path)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"status": "ok", "message": "Da luu noi dung tep thanh cong!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Khong the ghi tep: {str(e)}")
