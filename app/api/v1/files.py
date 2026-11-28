import os
import re
import json
import time
import glob
import socket
import shutil
import tarfile
import zipfile
import tempfile
import ipaddress
import platform
import subprocess
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.db.session import get_db_connection

router = APIRouter(prefix="/files", tags=["File Manager"])

MAX_EDIT_FILE_BYTES = 5 * 1024 * 1024        # 5 MB text editor cap
MAX_REMOTE_DOWNLOAD_BYTES = 512 * 1024 * 1024  # 512 MB cap
MAX_EXTRACT_BYTES = 1024 * 1024 * 1024       # 1 GB zip-bomb guard
MAX_EXTRACT_ENTRIES = 25000

PROTECTED_BASENAMES = {
    ".recycle_bin", ".ssh", ".bashrc", ".profile", ".bash_logout",
    "wp-config.php", ".htaccess", ".env"
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico"}
ARCHIVE_EXTS = (".zip", ".tar.gz", ".tgz", ".tar")
SKIP_WALK_DIRS = {".git", ".recycle_bin", "node_modules", "vendor", ".cache"}


# ==============================================================================
# Security & Path Helpers
# ==============================================================================

async def is_domain_owner(user_id: int, target_user: str) -> bool:
    """Kiểm tra Hosting User có sở hữu domain trỏ tới /home/<target_user> hay không."""
    try:
        db = await get_db_connection()
        try:
            cur = await db.execute(
                """
                SELECT d.id FROM domains d
                WHERE (d.user_id = ? OR d.user_id IN (SELECT id FROM users WHERE parent_id = ?))
                  AND (d.document_root = ? OR d.document_root LIKE ?)
                LIMIT 1
                """,
                (user_id, user_id, f"/home/{target_user}", f"/home/{target_user}/%"),
            )
            row = await cur.fetchone()
            return bool(row)
        finally:
            await db.close()
    except Exception:
        return False


def check_is_file_privileged(path: str) -> bool:
    try:
        if os.path.isfile(path):
            return True
    except Exception:
        pass
    if platform.system() == "Linux":
        try:
            res = subprocess.run(["sudo", "test", "-f", path], check=False)
            if res.returncode == 0:
                return True
        except Exception:
            pass
    return False


def check_is_dir_privileged(path: str) -> bool:
    try:
        if os.path.isdir(path):
            return True
    except Exception:
        pass
    if platform.system() == "Linux":
        try:
            res = subprocess.run(["sudo", "test", "-d", path], check=False)
            if res.returncode == 0:
                return True
        except Exception:
            pass
    return False


def _validate_single_name(name: str, label: str = "Tên mục") -> str:
    """Đảm bảo tên file/folder đơn lẻ không chứa path traversal hay ký tự nguy hiểm."""
    clean = (name or "").strip()
    if not clean or clean in (".", ".."):
        raise HTTPException(status_code=400, detail=f"{label} không hợp lệ.")
    if "\x00" in clean or "/" in clean or "\\" in clean:
        raise HTTPException(status_code=400, detail=f"{label} không được chứa ký tự '/', '\\' hoặc null byte.")
    return clean


def _assert_within_base(candidate_path: str, base_dir: str, follow_symlinks: bool = True) -> None:
    """Chặn thoát khỏi thư mục gốc (Path Traversal & Symlink Escape)."""
    abs_base = os.path.abspath(base_dir)
    abs_cand = os.path.abspath(candidate_path)
    try:
        Path(abs_cand).relative_to(Path(abs_base))
    except ValueError:
        raise HTTPException(status_code=403, detail="Phát hiện hành vi Path Traversal vượt phạm vi cho phép!")

    if follow_symlinks:
        real_base = os.path.realpath(base_dir)
        real_cand = os.path.realpath(candidate_path)
        try:
            Path(real_cand).relative_to(Path(real_base))
        except ValueError:
            raise HTTPException(status_code=403, detail="Symlink trỏ ra ngoài phạm vi thư mục được phép truy cập!")


async def resolve_base_and_target(
    user: dict,
    target_user: Optional[str],
    subpath: str,
    allow_root_self: bool = True,
) -> Tuple[str, str]:
    clean_u = "".join(c for c in target_user if c.isalnum() or c in ("_", "-")) if target_user else None

    if user.get("role") == "admin":
        base_dir = f"/home/{clean_u}" if clean_u else "/home"
    else:
        # Cô lập tuyệt đối: User nào chỉ được quản lý file của chính user đó
        # (Hosting user muốn quản lý file của Web App phải dùng Login as Web App User)
        own_u = user.get("username") or ""
        if not own_u:
            raise HTTPException(status_code=403, detail="Không xác định được thư mục người dùng.")
        if clean_u and clean_u != own_u:
            raise HTTPException(
                status_code=403,
                detail="Bạn chỉ được phép quản lý tệp tin của chính tài khoản hiện tại. Vui lòng dùng chức năng 'Login as Web App User' để truy cập tệp của Web Application."
            )
        base_dir = f"/home/{own_u}"

    clean_sub = (subpath or "").replace("\\", "/").lstrip("/")
    if "\x00" in clean_sub:
        raise HTTPException(status_code=400, detail="Đường dẫn chứa ký tự không hợp lệ.")

    joined = os.path.join(base_dir, clean_sub) if clean_sub else base_dir
    abs_base = os.path.abspath(base_dir)
    abs_target = os.path.abspath(joined)

    # Admin auto-resolve legacy "domains/<domain>/..." paths when target_user is omitted
    if (
        user.get("role") == "admin"
        and not clean_u
        and not os.path.exists(abs_target)
        and not check_is_file_privileged(abs_target)
        and not check_is_dir_privileged(abs_target)
        and clean_sub.startswith("domains/")
    ):
        parts = clean_sub.split("/")
        if len(parts) >= 2:
            dom_name = parts[1]
            try:
                matched = glob.glob(f"/home/*/domains/{dom_name}")
                if matched:
                    found_home = os.path.dirname(os.path.dirname(matched[0]))
                    cand = os.path.join(found_home, clean_sub)
                    if os.path.exists(cand) or check_is_file_privileged(cand) or check_is_dir_privileged(cand):
                        base_dir = found_home
                        abs_base = os.path.abspath(base_dir)
                        abs_target = os.path.abspath(cand)
            except Exception:
                pass

    _assert_within_base(abs_target, abs_base, follow_symlinks=True)
    if not allow_root_self and os.path.realpath(abs_target) == os.path.realpath(abs_base):
        raise HTTPException(status_code=403, detail="Không được phép thực hiện thao tác này lên thư mục gốc!")

    return os.path.realpath(base_dir), os.path.realpath(abs_target)


def resolve_safe_child(work_dir: str, item_name: str, base_dir: str, forbid_protected_self: bool = False) -> str:
    """Resolve một mục con trực tiếp trong work_dir an toàn tuyệt đối."""
    clean_name = _validate_single_name(item_name, "Tên tệp/thư mục")
    if forbid_protected_self and clean_name == ".recycle_bin":
        raise HTTPException(status_code=403, detail="Không được phép xóa hoặc đổi tên trực tiếp .recycle_bin!")
    candidate = os.path.join(work_dir, clean_name)
    _assert_within_base(candidate, base_dir, follow_symlinks=True)
    return candidate


def resolve_safe_subitem(work_dir: str, rel_name: str, base_dir: str) -> str:
    """Resolve một mục tương đối trong work_dir (cho phép 'sub/file.php' khi tìm kiếm đệ quy)."""
    clean_rel = (rel_name or "").replace("\\", "/").lstrip("/")
    if not clean_rel or clean_rel in (".", "..") or "\x00" in clean_rel:
        raise HTTPException(status_code=400, detail="Tên tệp không hợp lệ.")
    if any(seg in ("", ".", "..") for seg in clean_rel.split("/")):
        raise HTTPException(status_code=403, detail="Đường dẫn chứa thành phần '..' không hợp lệ!")
    candidate = os.path.join(work_dir, clean_rel)
    _assert_within_base(candidate, base_dir, follow_symlinks=True)
    return candidate


def sync_owner(path: str):
    """Đồng bộ owner:group chuẩn xác với user sở hữu thư mục /home/<user>."""
    try:
        m = re.match(r"^/home/([^/]+)", os.path.abspath(path))
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


def get_recycle_bin_dir(base_dir: str) -> str:
    """Tạo/lấy thư mục .recycle_bin bên trong base_dir của user (nếu admin ở /home thì không dùng chung)."""
    real_base = os.path.realpath(base_dir)
    if real_base == "/home":
        r_dir = "/tmp/presspanel_admin_recycle_bin"
    else:
        r_dir = os.path.join(real_base, ".recycle_bin")
    try:
        os.makedirs(r_dir, exist_ok=True)
    except PermissionError:
        subprocess.run(["sudo", "mkdir", "-p", r_dir], check=False)
        sync_owner(r_dir)
    return r_dir


def _assert_safe_public_url(url: str) -> urllib.parse.ParseResult:
    """Chặn SSRF: chỉ cho phép HTTP/HTTPS tới IP Public, cấm mọi IP Private/Loopback/Metadata."""
    parsed = urllib.parse.urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="URL không hợp lệ (chỉ hỗ trợ http:// hoặc https://)")

    host = parsed.hostname.strip().lower()
    if host in ("localhost", "ip6-localhost") or host.endswith(".local") or host.endswith(".internal"):
        raise HTTPException(status_code=403, detail="Bị chặn vì lý do bảo mật SSRF (không cho phép truy cập địa chỉ nội bộ).")

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail=f"Không thể phân giải tên miền: {host}")

    if not infos:
        raise HTTPException(status_code=400, detail="Không tìm thấy địa chỉ IP cho URL chỉ định.")

    for info in infos:
        ip_str = info[4][0].split("%")[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Địa chỉ IP của URL không hợp lệ.")
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_reserved
            or ip_obj.is_multicast
            or ip_obj.is_unspecified
        ):
            raise HTTPException(status_code=403, detail=f"Bị chặn bởi SSRF Guard: IP đích ({ip_str}) thuộc mạng nội bộ/loopback.")

    return parsed


def format_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.2f} KB"
    if b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.2f} MB"
    return f"{b / (1024 * 1024 * 1024):.2f} GB"


def _is_protected_item(name: str, rel_dir: str = "") -> bool:
    base_name = os.path.basename(name)
    if base_name in PROTECTED_BASENAMES:
        return True
    if not rel_dir and base_name in ("domains", "public_html", "logs", "ssl"):
        return True
    return False


# ==============================================================================
# Pydantic Models
# ==============================================================================

class FileContentUpdate(BaseModel):
    path: str
    content: str
    target_user: Optional[str] = None


class CreateItemRequest(BaseModel):
    path: str
    item_type: str  # "file" | "folder"
    target_user: Optional[str] = None


class DeleteItemRequest(BaseModel):
    path: str
    use_recycle_bin: bool = True
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
    mode: str  # "755", "644"
    recursive: bool = False
    target_user: Optional[str] = None


class FixPermsRequest(BaseModel):
    current_path: str = ""
    target_user: Optional[str] = None


class RemoteDownloadRequest(BaseModel):
    current_path: str = ""
    url: str
    filename: Optional[str] = None
    target_user: Optional[str] = None


class PasteRequest(BaseModel):
    action: str  # "copy" | "cut"
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


class ExtractRequest(BaseModel):
    current_path: str = ""
    name: str
    dest_subdir: Optional[str] = ""
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
# Endpoints: List, Read, Edit, Create, Upload
# ==============================================================================

@router.get("/list")
async def list_files(
    path: str = Query("", description="Đường dẫn tương đối"),
    target_user: Optional[str] = Query(None, description="Username mục tiêu (Admin)"),
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm"),
    include_subdir: bool = Query(False, description="Tìm đệ quy thư mục con"),
    current_user: dict = Depends(get_current_user),
):
    base_dir, target_dir = await resolve_base_and_target(current_user, target_user, path)
    if not os.path.exists(target_dir) and not check_is_dir_privileged(target_dir):
        if path == "":
            try:
                os.makedirs(target_dir, exist_ok=True)
            except Exception:
                pass
        if not check_is_dir_privileged(target_dir):
            return {
                "items": [],
                "current_path": path,
                "absolute_path": target_dir,
                "base_dir": base_dir,
                "target_user": target_user or current_user["username"],
            }

    # Search across subdirectories
    if search and include_subdir:
        q = search.lower().strip()
        matched_items = []
        max_results = 200
        count = 0
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_WALK_DIRS]
            rel_dir = os.path.relpath(root, target_dir)
            if rel_dir == ".":
                rel_dir = ""

            for d in dirs:
                if q in d.lower():
                    item_rel = os.path.join(rel_dir, d).replace("\\", "/")
                    full_p = os.path.join(root, d)
                    try:
                        st = os.lstat(full_p)
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
                        "symlink_safe": True,
                        "size": 0,
                        "size_formatted": None,
                        "modified": mtime,
                        "mode": mode_oct,
                        "owner": target_user or current_user["username"],
                        "pmsn_owner": f"{mode_oct} / {target_user or current_user['username']}",
                        "protected": _is_protected_item(d, rel_dir),
                        "is_archive": False,
                        "is_image": False,
                    })
                    count += 1
                    if count >= max_results:
                        break

            for f in files:
                if q in f.lower():
                    item_rel = os.path.join(rel_dir, f).replace("\\", "/")
                    full_p = os.path.join(root, f)
                    try:
                        st = os.lstat(full_p)
                        sz = st.st_size
                        mode_oct = oct(st.st_mode)[-3:]
                        mtime = int(st.st_mtime)
                    except Exception:
                        sz = 0
                        mode_oct = "644"
                        mtime = int(time.time())
                    low = f.lower()
                    ext = os.path.splitext(low)[1]
                    matched_items.append({
                        "name": item_rel,
                        "is_dir": False,
                        "is_symlink": os.path.islink(full_p),
                        "link_target": "",
                        "symlink_safe": True,
                        "size": sz,
                        "size_formatted": format_bytes(sz),
                        "modified": mtime,
                        "mode": mode_oct,
                        "owner": target_user or current_user["username"],
                        "pmsn_owner": f"{mode_oct} / {target_user or current_user['username']}",
                        "protected": _is_protected_item(f, rel_dir),
                        "is_archive": low.endswith(ARCHIVE_EXTS),
                        "is_image": ext in IMAGE_EXTS,
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
            "base_dir": base_dir,
            "target_user": target_user or current_user["username"],
            "is_search": True,
        }

    items = []
    try:
        with os.scandir(target_dir) as entries:
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                    is_symlink = entry.is_symlink()
                    link_target = ""
                    symlink_safe = True
                    if is_symlink:
                        try:
                            link_target = os.readlink(entry.path)
                            real_t = os.path.realpath(entry.path)
                            Path(real_t).relative_to(Path(base_dir))
                        except Exception:
                            symlink_safe = False

                    real_path = os.path.realpath(entry.path)
                    is_directory = os.path.isdir(real_path) if symlink_safe else False

                    owner_name = "root"
                    try:
                        import pwd
                        owner_name = pwd.getpwuid(info.st_uid).pw_name
                    except Exception:
                        pass

                    mode_oct = oct(info.st_mode)[-3:]
                    low = entry.name.lower()
                    ext = os.path.splitext(low)[1]

                    items.append({
                        "name": entry.name,
                        "is_dir": is_directory,
                        "is_symlink": is_symlink,
                        "link_target": link_target,
                        "symlink_safe": symlink_safe,
                        "size": info.st_size if not is_directory else 0,
                        "size_formatted": None if is_directory else format_bytes(info.st_size),
                        "modified": int(info.st_mtime),
                        "mode": mode_oct,
                        "owner": owner_name,
                        "pmsn_owner": f"{mode_oct} / {owner_name}",
                        "protected": _is_protected_item(entry.name, path),
                        "is_archive": (not is_directory) and low.endswith(ARCHIVE_EXTS),
                        "is_image": (not is_directory) and ext in IMAGE_EXTS,
                    })
                except Exception:
                    continue
    except PermissionError:
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
                        symlink_safe = True

                        if " -> " in raw_name:
                            clean_name, link_target = raw_name.split(" -> ", 1)
                            clean_name = clean_name.strip()
                            link_target = link_target.strip()
                            full_target = link_target if link_target.startswith("/") else os.path.join(target_dir, link_target)
                            try:
                                Path(os.path.realpath(full_target)).relative_to(Path(base_dir))
                                if os.path.isdir(os.path.realpath(full_target)):
                                    is_directory = True
                            except Exception:
                                symlink_safe = False
                                is_directory = False

                        try:
                            size = int(parts[4])
                        except ValueError:
                            size = 0
                        try:
                            mtime = int(parts[5])
                        except ValueError:
                            mtime = 0

                        mode_code = "755" if is_directory else "644"
                        low = clean_name.lower()
                        ext = os.path.splitext(low)[1]
                        items.append({
                            "name": clean_name,
                            "is_dir": is_directory,
                            "is_symlink": is_symlink,
                            "link_target": link_target,
                            "symlink_safe": symlink_safe,
                            "size": size if not is_directory else 0,
                            "size_formatted": None if is_directory else format_bytes(size),
                            "modified": mtime,
                            "mode": mode_code,
                            "owner": owner,
                            "pmsn_owner": f"{mode_code} / {owner}",
                            "protected": _is_protected_item(clean_name, path),
                            "is_archive": (not is_directory) and low.endswith(ARCHIVE_EXTS),
                            "is_image": (not is_directory) and ext in IMAGE_EXTS,
                        })
        except Exception:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập thư mục này trên máy chủ.")

    sorted_items = sorted(items, key=lambda x: (not x["is_dir"], x["name"].lower()))
    return {
        "items": sorted_items,
        "current_path": path,
        "absolute_path": target_dir,
        "base_dir": base_dir,
        "target_user": target_user or current_user["username"],
    }


@router.get("/content")
async def get_file_content(
    path: str = Query(..., description="Đường dẫn file"),
    target_user: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    _, file_path = await resolve_base_and_target(current_user, target_user, path, allow_root_self=False)
    if not check_is_file_privileged(file_path):
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp tin.")

    raw_bytes: Optional[bytes] = None
    try:
        sz = os.path.getsize(file_path)
        if sz > MAX_EDIT_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"Tệp tin quá lớn ({format_bytes(sz)}). Trình soạn thảo chỉ hỗ trợ tối đa 5 MB.")
        with open(file_path, "rb") as f:
            raw_bytes = f.read(MAX_EDIT_FILE_BYTES + 1)
    except HTTPException:
        raise
    except Exception:
        pass

    if raw_bytes is None and platform.system() == "Linux":
        try:
            proc_sz = subprocess.run(["sudo", "stat", "-c", "%s", file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            if proc_sz.returncode == 0 and proc_sz.stdout.strip().isdigit():
                sz = int(proc_sz.stdout.strip())
                if sz > MAX_EDIT_FILE_BYTES:
                    raise HTTPException(status_code=413, detail=f"Tệp tin quá lớn ({format_bytes(sz)}). Trình soạn thảo chỉ hỗ trợ tối đa 5 MB.")
            proc = subprocess.run(["sudo", "cat", file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if proc.returncode == 0:
                raw_bytes = proc.stdout
        except HTTPException:
            raise
        except Exception:
            pass

    if raw_bytes is None:
        raise HTTPException(status_code=403, detail="Không có quyền đọc tệp tin này.")

    if b"\x00" in raw_bytes[:8192]:
        raise HTTPException(status_code=400, detail="Đây là tệp nhị phân (Binary), không thể mở bằng trình soạn thảo văn bản. Hãy dùng chức năng Tải xuống hoặc Xem trước ảnh.")

    return {
        "path": path,
        "content": raw_bytes.decode("utf-8", errors="replace"),
        "absolute_path": file_path,
        "size": len(raw_bytes),
    }


@router.put("/content")
async def save_file_content(
    req: FileContentUpdate,
    current_user: dict = Depends(get_current_user),
):
    _, file_path = await resolve_base_and_target(current_user, req.target_user, req.path, allow_root_self=False)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        sync_owner(file_path)
        return {"status": "ok", "message": "Đã lưu nội dung tệp tin thành công!"}
    except Exception:
        tmp_fd, tmp_save_path = tempfile.mkstemp(prefix="pp_edit_", suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as ftmp:
                ftmp.write(req.content)
            proc_sudo = subprocess.run(["sudo", "cp", tmp_save_path, file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            if proc_sudo.returncode == 0:
                sync_owner(file_path)
                return {"status": "ok", "message": "Đã lưu nội dung tệp tin thành công!"}
            raise HTTPException(status_code=403, detail="Không có quyền ghi tệp tin này.")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Không thể lưu tệp: {str(e)}")
        finally:
            if os.path.exists(tmp_save_path):
                os.remove(tmp_save_path)


@router.post("/create")
async def create_file_or_dir(
    req: CreateItemRequest,
    current_user: dict = Depends(get_current_user),
):
    base_dir, target_path = await resolve_base_and_target(current_user, req.target_user, req.path, allow_root_self=False)
    _validate_single_name(os.path.basename(target_path), "Tên tệp/thư mục")
    parent_dir = os.path.dirname(target_path)
    _assert_within_base(parent_dir, base_dir, follow_symlinks=True)

    if os.path.exists(target_path) or check_is_file_privileged(target_path) or check_is_dir_privileged(target_path):
        raise HTTPException(status_code=400, detail=f"'{os.path.basename(target_path)}' đã tồn tại.")

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

        created = False
        try:
            os.makedirs(parent_dir, exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write("")
            os.chmod(target_path, 0o644)
            created = True
        except PermissionError:
            subprocess.run(["sudo", "mkdir", "-p", parent_dir], check=False)
            proc = subprocess.run(["sudo", "touch", target_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode == 0:
                subprocess.run(["sudo", "chmod", "644", target_path], check=False)
                created = True

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
    current_user: dict = Depends(get_current_user),
):
    base_dir, work_dir = await resolve_base_and_target(current_user, target_user, current_path)
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=404, detail="Thư mục làm việc không tồn tại.")

    saved_files = []
    for f in files:
        safe_fn = _validate_single_name(os.path.basename(f.filename or ""), "Tên tệp tải lên")
        dest_path = resolve_safe_child(work_dir, safe_fn, base_dir)
        tmp_dest = None
        try:
            try:
                with open(dest_path, "wb") as buffer:
                    shutil.copyfileobj(f.file, buffer)
                os.chmod(dest_path, 0o644)
            except PermissionError:
                tmp_fd, tmp_dest = tempfile.mkstemp(prefix="pp_up_", suffix=".tmp")
                with os.fdopen(tmp_fd, "wb") as buffer:
                    shutil.copyfileobj(f.file, buffer)
                subprocess.run(["sudo", "cp", tmp_dest, dest_path], check=True)
                subprocess.run(["sudo", "chmod", "644", dest_path], check=False)

            sync_owner(dest_path)
            saved_files.append(safe_fn)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lỗi tải lên {safe_fn}: {str(e)}")
        finally:
            if tmp_dest and os.path.exists(tmp_dest):
                os.remove(tmp_dest)

    return {"status": "ok", "message": f"Đã tải lên thành công {len(saved_files)} tệp tin!", "files": saved_files}


# ==============================================================================
# Endpoints: Size, Delete, Recycle Bin, Rename, Chmod, Fix Perms
# ==============================================================================

@router.post("/calculate-size")
async def calculate_size(
    req: CalculateSizeRequest,
    current_user: dict = Depends(get_current_user),
):
    base_dir, work_dir = await resolve_base_and_target(current_user, req.target_user, req.current_path)
    if req.name == ".":
        target_path = work_dir
    else:
        target_path = resolve_safe_subitem(work_dir, req.name, base_dir)

    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Không tìm thấy mục chỉ định.")

    total_bytes = 0
    if os.path.isfile(target_path):
        total_bytes = os.path.getsize(target_path)
    else:
        try:
            proc = subprocess.run(["du", "-sb", target_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
            if proc.returncode == 0 and proc.stdout.strip():
                total_bytes = int(proc.stdout.split()[0])
            else:
                raise RuntimeError("du fallback")
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
        "formatted": format_bytes(total_bytes),
    }


def _load_recycle_metadata(meta_file: str, recycle_dir: str = "") -> List[Dict[str, Any]]:
    metadata: List[Dict[str, Any]] = []
    raw_text: Optional[str] = None
    try:
        if os.path.isfile(meta_file):
            with open(meta_file, "r", encoding="utf-8") as mf:
                raw_text = mf.read()
    except Exception:
        raw_text = None

    if raw_text is None and platform.system() == "Linux":
        try:
            proc = subprocess.run(["sudo", "cat", meta_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            if proc.returncode == 0 and proc.stdout.strip():
                raw_text = proc.stdout
        except Exception:
            pass

    if raw_text:
        try:
            data = json.loads(raw_text)
            if isinstance(data, list):
                metadata = data
        except Exception:
            metadata = []

    if not recycle_dir:
        return metadata

    # Quét thực tế các mục đang có trong .recycle_bin để đồng bộ & tránh trống oan
    actual_entries: Dict[str, bool] = {}
    try:
        with os.scandir(recycle_dir) as entries:
            for e in entries:
                if e.name != "metadata.json":
                    actual_entries[e.name] = e.is_dir(follow_symlinks=False)
    except Exception:
        if platform.system() == "Linux":
            try:
                proc = subprocess.run(["sudo", "ls", "-1Ap", recycle_dir], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
                if proc.returncode == 0:
                    for line in proc.stdout.splitlines():
                        line = line.strip()
                        if not line or line == "metadata.json":
                            continue
                        is_d = line.endswith("/")
                        actual_entries[line.rstrip("/")] = is_d
            except Exception:
                pass

    if not actual_entries:
        return []

    known_names = set()
    synced: List[Dict[str, Any]] = []
    for m in metadata:
        rn = m.get("recycle_name")
        if rn and rn in actual_entries:
            synced.append(m)
            known_names.add(rn)

    base_parent = os.path.dirname(recycle_dir)
    for rn, is_d in actual_entries.items():
        if rn not in known_names:
            parts = rn.split("_", 2)
            orig_name = parts[2] if len(parts) == 3 and parts[0].isdigit() else (rn.split("_", 1)[1] if "_" in rn and rn.split("_", 1)[0].isdigit() else rn)
            synced.append({
                "id": parts[0] if parts[0].isdigit() else rn,
                "original_name": orig_name,
                "recycle_name": rn,
                "original_path": os.path.join(base_parent, orig_name),
                "deleted_at": int(time.time()),
                "is_dir": is_d,
            })

    return synced


def _save_recycle_metadata(meta_file: str, metadata: List[Dict[str, Any]], owner_ref: str):
    try:
        with open(meta_file, "w", encoding="utf-8") as mf:
            json.dump(metadata, mf, indent=2, ensure_ascii=False)
        os.chmod(meta_file, 0o644)
        sync_owner(meta_file)
    except Exception:
        tmp_fd, tmp_p = tempfile.mkstemp(prefix="pp_meta_", suffix=".json")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as mf:
                json.dump(metadata, mf, indent=2, ensure_ascii=False)
            subprocess.run(["sudo", "cp", tmp_p, meta_file], check=False)
            subprocess.run(["sudo", "chmod", "644", meta_file], check=False)
            sync_owner(meta_file)
        finally:
            if os.path.exists(tmp_p):
                os.remove(tmp_p)


@router.post("/batch-delete")
async def batch_delete_files(
    req: BatchDeleteRequest,
    current_user: dict = Depends(get_current_user),
):
    base_dir, work_dir = await resolve_base_and_target(current_user, req.target_user, req.current_path)
    if not req.items:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một mục để xóa.")

    recycle_dir = get_recycle_bin_dir(base_dir) if req.use_recycle_bin else ""
    meta_file = os.path.join(recycle_dir, "metadata.json") if req.use_recycle_bin else ""
    metadata = _load_recycle_metadata(meta_file, recycle_dir) if req.use_recycle_bin else []

    deleted_count = 0
    for item_name in req.items:
        src = resolve_safe_subitem(work_dir, item_name, base_dir)
        if os.path.basename(src) == ".recycle_bin" or os.path.realpath(src) == os.path.realpath(base_dir):
            continue
        if not os.path.exists(src) and not os.path.islink(src):
            continue

        is_d = os.path.isdir(src) and not os.path.islink(src)
        if req.use_recycle_bin:
            ts = int(time.time() * 1000)
            base_n = os.path.basename(src)
            recycle_name = f"{ts}_{deleted_count}_{base_n}"
            dest = os.path.join(recycle_dir, recycle_name)
            moved = False
            try:
                shutil.move(src, dest)
                moved = True
            except Exception:
                proc = subprocess.run(["sudo", "mv", src, dest], check=False)
                moved = proc.returncode == 0
            if moved:
                metadata.append({
                    "id": str(ts),
                    "original_name": base_n,
                    "recycle_name": recycle_name,
                    "original_path": src,
                    "deleted_at": int(time.time()),
                    "is_dir": is_d,
                })
                deleted_count += 1
        else:
            try:
                if os.path.islink(src) or not is_d:
                    os.remove(src)
                else:
                    shutil.rmtree(src)
                deleted_count += 1
            except Exception:
                proc = subprocess.run(["sudo", "rm", "-rf", src], check=False)
                if proc.returncode == 0:
                    deleted_count += 1

    if req.use_recycle_bin and deleted_count > 0:
        _save_recycle_metadata(meta_file, metadata, recycle_dir)

    action_text = "chuyển vào Thùng rác" if req.use_recycle_bin else "xóa vĩnh viễn"
    return {"status": "ok", "message": f"Đã {action_text} {deleted_count} mục thành công!", "count": deleted_count}


@router.delete("/delete")
async def delete_file_or_dir(
    req: DeleteItemRequest,
    current_user: dict = Depends(get_current_user),
):
    base_dir, target_path = await resolve_base_and_target(current_user, req.target_user, req.path, allow_root_self=False)
    if os.path.basename(target_path) == ".recycle_bin":
        raise HTTPException(status_code=403, detail="Không thể xóa trực tiếp thư mục .recycle_bin.")
    if not os.path.exists(target_path) and not os.path.islink(target_path):
        raise HTTPException(status_code=404, detail="Mục cần xóa không tồn tại.")

    item_base = os.path.basename(target_path)
    is_d = os.path.isdir(target_path) and not os.path.islink(target_path)

    if req.use_recycle_bin:
        recycle_dir = get_recycle_bin_dir(base_dir)
        meta_file = os.path.join(recycle_dir, "metadata.json")
        metadata = _load_recycle_metadata(meta_file, recycle_dir)
        ts = int(time.time() * 1000)
        recycle_name = f"{ts}_{item_base}"
        dest = os.path.join(recycle_dir, recycle_name)
        try:
            shutil.move(target_path, dest)
        except Exception:
            proc = subprocess.run(["sudo", "mv", target_path, dest], check=False)
            if proc.returncode != 0:
                raise HTTPException(status_code=500, detail="Không thể chuyển mục vào thùng rác.")
        metadata.append({
            "id": str(ts),
            "original_name": item_base,
            "recycle_name": recycle_name,
            "original_path": target_path,
            "deleted_at": int(time.time()),
            "is_dir": is_d,
        })
        _save_recycle_metadata(meta_file, metadata, recycle_dir)
        return {"status": "ok", "message": f"Đã chuyển {item_base} vào Thùng rác!"}

    try:
        if os.path.islink(target_path) or not is_d:
            os.remove(target_path)
        else:
            shutil.rmtree(target_path)
        return {"status": "ok", "message": f"Đã xóa vĩnh viễn {item_base} thành công!"}
    except Exception:
        proc = subprocess.run(["sudo", "rm", "-rf", target_path], check=False)
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail="Không thể xóa mục chỉ định.")
        return {"status": "ok", "message": f"Đã xóa vĩnh viễn {item_base} thành công!"}


@router.get("/recycle-bin")
async def get_recycle_bin(
    target_user: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    base_dir, _ = await resolve_base_and_target(current_user, target_user, "")
    recycle_dir = get_recycle_bin_dir(base_dir)
    meta_file = os.path.join(recycle_dir, "metadata.json")
    items = _load_recycle_metadata(meta_file, recycle_dir)
    return {"status": "ok", "items": list(reversed(items)), "count": len(items)}


@router.post("/recycle-bin/restore")
async def restore_from_recycle_bin(
    recycle_name: str = Query(...),
    target_user: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    base_dir, _ = await resolve_base_and_target(current_user, target_user, "")
    recycle_dir = get_recycle_bin_dir(base_dir)
    meta_file = os.path.join(recycle_dir, "metadata.json")
    metadata = _load_recycle_metadata(meta_file, recycle_dir)
    if not metadata:
        raise HTTPException(status_code=404, detail="Thùng rác trống.")

    item = next((m for m in metadata if m.get("recycle_name") == recycle_name or m.get("id") == recycle_name), None)
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy mục trong thùng rác.")

    safe_rec_name = _validate_single_name(item["recycle_name"], "Tên mục thùng rác")
    src = os.path.join(recycle_dir, safe_rec_name)
    dest = item.get("original_path") or ""
    _assert_within_base(dest, base_dir, follow_symlinks=False)

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        dest = f"{dest}.restored_{int(time.time())}"

    try:
        shutil.move(src, dest)
    except Exception:
        proc = subprocess.run(["sudo", "mv", src, dest], check=False)
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail="Không thể khôi phục tệp tin.")
    sync_owner(dest)

    new_meta = [m for m in metadata if m.get("recycle_name") != item["recycle_name"]]
    _save_recycle_metadata(meta_file, new_meta, recycle_dir)
    return {"status": "ok", "message": f"Đã khôi phục {item['original_name']} thành công!"}


@router.post("/recycle-bin/empty")
async def empty_recycle_bin(
    target_user: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    base_dir, _ = await resolve_base_and_target(current_user, target_user, "")
    recycle_dir = get_recycle_bin_dir(base_dir)
    try:
        with os.scandir(recycle_dir) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        shutil.rmtree(entry.path)
                    else:
                        os.remove(entry.path)
                except Exception:
                    subprocess.run(["sudo", "rm", "-rf", entry.path], check=False)
    except Exception:
        subprocess.run(["sudo", "rm", "-rf", recycle_dir], check=False)
        subprocess.run(["sudo", "mkdir", "-p", recycle_dir], check=False)
        sync_owner(recycle_dir)

    return {"status": "ok", "message": "Đã dọn sạch thùng rác hoàn toàn!"}


@router.post("/rename")
async def rename_item(
    req: RenameRequest,
    current_user: dict = Depends(get_current_user),
):
    base_dir, work_dir = await resolve_base_and_target(current_user, req.target_user, req.current_path)
    old_p = resolve_safe_subitem(work_dir, req.old_name, base_dir)
    old_parent = os.path.dirname(old_p)
    clean_new = _validate_single_name(req.new_name, "Tên mới")
    if os.path.basename(old_p) == ".recycle_bin" or clean_new == ".recycle_bin":
        raise HTTPException(status_code=403, detail="Không thể đổi tên liên quan đến .recycle_bin.")
    new_p = resolve_safe_child(old_parent, clean_new, base_dir)

    if not os.path.exists(old_p) and not os.path.islink(old_p):
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
        return {"status": "ok", "message": f"Đổi tên thành công sang {clean_new}!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi đổi tên: {str(e)}")


@router.post("/chmod")
async def chmod_item(
    req: ChmodRequest,
    current_user: dict = Depends(get_current_user),
):
    clean_mode = (req.mode or "").strip()
    if not re.fullmatch(r"[0-7]{3}", clean_mode):
        raise HTTPException(status_code=400, detail="Mã phân quyền không hợp lệ (chỉ chấp nhận 3 chữ số Octal 000-777, ví dụ 755 hoặc 644).")

    base_dir, work_dir = await resolve_base_and_target(current_user, req.target_user, req.current_path)
    target = resolve_safe_subitem(work_dir, req.name, base_dir)
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="Mục chỉ định không tồn tại.")

    try:
        mode_val = int(clean_mode, 8)
        if req.recursive and os.path.isdir(target):
            proc = subprocess.run(["sudo", "chmod", "-R", clean_mode, target], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode != 0:
                raise Exception(proc.stderr or "sudo chmod -R failed")
        else:
            try:
                os.chmod(target, mode_val)
            except PermissionError:
                proc = subprocess.run(["sudo", "chmod", clean_mode, target], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode != 0:
                    raise Exception(proc.stderr or "sudo chmod failed")

        return {"status": "ok", "message": f"Phân quyền {req.name} thành {clean_mode} thành công!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi phân quyền: {str(e)}")


@router.post("/fix-permissions")
async def fix_directory_permissions(
    req: FixPermsRequest,
    current_user: dict = Depends(get_current_user),
):
    """Chuẩn hóa nhanh quyền an toàn web chuẩn (Thư mục 755, Tệp tin 644) & sync owner trong thư mục hiện tại."""
    base_dir, work_dir = await resolve_base_and_target(current_user, req.target_user, req.current_path)
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=404, detail="Thư mục không tồn tại.")

    if os.path.realpath(work_dir) == "/home":
        raise HTTPException(status_code=403, detail="Vui lòng chọn vào thư mục website cụ thể thay vì toàn bộ /home.")

    try:
        subprocess.run(["sudo", "find", work_dir, "-type", "d", "-exec", "chmod", "755", "{}", "+"], check=False, timeout=60)
        subprocess.run(["sudo", "find", work_dir, "-type", "f", "-exec", "chmod", "644", "{}", "+"], check=False, timeout=60)
        sync_owner(work_dir)
        return {"status": "ok", "message": "Đã chuẩn hóa phân quyền (Thư mục 755 / Tập tin 644) và đồng bộ Owner thành công!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi chuẩn hóa quyền: {str(e)}")


# ==============================================================================
# Endpoints: Remote Download (SSRF Hardened), Copy/Cut/Paste, Download, Zip, Unzip, Grep
# ==============================================================================

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@router.post("/remote-download")
async def remote_download(
    req: RemoteDownloadRequest,
    current_user: dict = Depends(get_current_user),
):
    base_dir, work_dir = await resolve_base_and_target(current_user, req.target_user, req.current_path)
    current_url = (req.url or "").strip()

    # Follow tối đa 5 redirect và kiểm tra SSRF ở TỪNG hop trước khi tải
    final_response = None
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        for _ in range(6):
            _assert_safe_public_url(current_url)
            u_req = urllib.request.Request(
                current_url,
                headers={"User-Agent": "PressPanel-FileManager/1.0"}
            )
            try:
                final_response = opener.open(u_req, timeout=20)
                break
            except urllib.error.HTTPError as he:
                if he.code in (301, 302, 303, 307, 308):
                    loc = he.headers.get("Location")
                    he.close()
                    if not loc:
                        raise HTTPException(status_code=400, detail="Redirect URL rỗng.")
                    current_url = urllib.parse.urljoin(current_url, loc)
                    continue
                detail_msg = f"Máy chủ nguồn trả về lỗi HTTP {he.code}."
                he.close()
                raise HTTPException(status_code=400, detail=detail_msg)
        else:
            raise HTTPException(status_code=400, detail="Quá nhiều chuyển hướng HTTP (Redirect limit).")

        parsed_final = urllib.parse.urlparse(current_url)
        raw_fn = req.filename.strip() if (req.filename and req.filename.strip()) else os.path.basename(parsed_final.path)
        raw_fn = raw_fn or "downloaded_file"
        safe_fn = _validate_single_name(os.path.basename(raw_fn), "Tên tệp tải về")
        dest = resolve_safe_child(work_dir, safe_fn, base_dir)

        total = 0
        tmp_fd, tmp_dl = tempfile.mkstemp(prefix="pp_rem_", suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "wb") as out_f:
                while True:
                    chunk = final_response.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_REMOTE_DOWNLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="Tệp tải từ xa vượt quá giới hạn tối đa 512 MB.")
                    out_f.write(chunk)
            try:
                shutil.move(tmp_dl, dest)
                os.chmod(dest, 0o644)
            except PermissionError:
                subprocess.run(["sudo", "mv", tmp_dl, dest], check=True)
                subprocess.run(["sudo", "chmod", "644", dest], check=False)
            sync_owner(dest)
            return {"status": "ok", "message": f"Tải tệp {safe_fn} ({format_bytes(total)}) từ xa thành công!", "filename": safe_fn}
        finally:
            if os.path.exists(tmp_dl):
                os.remove(tmp_dl)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi tải file từ xa: {str(e)}")
    finally:
        if final_response is not None:
            try:
                final_response.close()
            except Exception:
                pass


@router.post("/paste")
async def paste_items(
    req: PasteRequest,
    current_user: dict = Depends(get_current_user),
):
    if req.action not in ("copy", "cut"):
        raise HTTPException(status_code=400, detail="Hành động paste chỉ hỗ trợ 'copy' hoặc 'cut'.")

    base_dir, src_work_dir = await resolve_base_and_target(current_user, req.target_user, req.src_path)
    _, dest_work_dir = await resolve_base_and_target(current_user, req.target_user, req.dest_path)

    pasted = 0
    for item in req.items:
        src = resolve_safe_subitem(src_work_dir, item, base_dir)
        item_base = _validate_single_name(os.path.basename(src), "Tên mục")
        dest = resolve_safe_child(dest_work_dir, item_base, base_dir)
        if not os.path.exists(src):
            continue
        if os.path.realpath(src) == os.path.realpath(dest):
            continue
        if os.path.isdir(src) and os.path.realpath(dest).startswith(os.path.realpath(src) + os.sep):
            raise HTTPException(status_code=400, detail=f"Không thể dán thư mục '{item_base}' vào chính thư mục con của nó!")

        try:
            if req.action == "cut":
                try:
                    shutil.move(src, dest)
                except PermissionError:
                    subprocess.run(["sudo", "mv", src, dest], check=True)
            else:
                try:
                    if os.path.isdir(src):
                        shutil.copytree(src, dest, symlinks=False, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src, dest, follow_symlinks=False)
                except PermissionError:
                    if os.path.isdir(src):
                        subprocess.run(["sudo", "cp", "-r", src, dest], check=True)
                    else:
                        subprocess.run(["sudo", "cp", src, dest], check=True)

            sync_owner(dest)
            pasted += 1
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lỗi dán tệp {item}: {str(e)}")

    return {"status": "ok", "message": f"Đã dán thành công {pasted} mục!", "count": pasted}


@router.get("/download")
async def download_file(
    path: str = Query(..., description="Đường dẫn file"),
    inline: bool = Query(False, description="Xem trước nội tuyến (ảnh)"),
    target_user: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    from starlette.background import BackgroundTask
    _, file_path = await resolve_base_and_target(current_user, target_user, path, allow_root_self=False)
    if not check_is_file_privileged(file_path):
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp tin.")

    disp = "inline" if inline else "attachment"
    fn = os.path.basename(file_path)
    try:
        with open(file_path, "rb"):
            pass
        return FileResponse(file_path, filename=None if inline else fn, content_disposition_type=disp)
    except Exception:
        if platform.system() == "Linux":
            tmp_fd, tmp_dl = tempfile.mkstemp(prefix="pp_dl_")
            os.close(tmp_fd)
            proc = subprocess.run(["sudo", "cp", file_path, tmp_dl], check=False)
            subprocess.run(["sudo", "chmod", "644", tmp_dl], check=False)
            if proc.returncode == 0 and os.path.exists(tmp_dl):
                return FileResponse(
                    tmp_dl,
                    filename=None if inline else fn,
                    content_disposition_type=disp,
                    background=BackgroundTask(lambda: os.remove(tmp_dl) if os.path.exists(tmp_dl) else None),
                )
            if os.path.exists(tmp_dl):
                os.remove(tmp_dl)
    raise HTTPException(status_code=403, detail="Không có quyền tải xuống tệp tin này.")


@router.post("/compress")
async def compress_files(
    req: ArchiveRequest,
    current_user: dict = Depends(get_current_user),
):
    if not req.items:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất một tệp hoặc thư mục để nén.")

    base_dir, work_dir = await resolve_base_and_target(current_user, req.target_user, req.current_path)
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=404, detail="Thư mục làm việc không tồn tại.")

    archive_name = _validate_single_name(req.archive_name.strip(), "Tên tệp nén")
    if req.format == "zip" and not archive_name.endswith(".zip"):
        archive_name += ".zip"
    elif req.format == "tar" and not archive_name.endswith(".tar"):
        archive_name += ".tar"
    elif req.format in ("tar.gz", "tgz", "gz") and not (archive_name.endswith(".tar.gz") or archive_name.endswith(".tgz")):
        archive_name += ".tar.gz"
    elif req.format not in ("zip", "tar", "tar.gz", "tgz", "gz"):
        raise HTTPException(status_code=400, detail="Định dạng nén không hợp lệ.")

    archive_full_path = resolve_safe_child(work_dir, archive_name, base_dir)
    tmp_fd, target_dest = tempfile.mkstemp(prefix="pp_arc_", suffix=".tmp")
    os.close(tmp_fd)

    try:
        if req.format == "zip":
            with zipfile.ZipFile(target_dest, "w", zipfile.ZIP_DEFLATED) as zf:
                for item_name in req.items:
                    item_full = resolve_safe_subitem(work_dir, item_name, base_dir)
                    if os.path.islink(item_full):
                        continue
                    if os.path.isdir(item_full):
                        for root, dirs, files in os.walk(item_full, followlinks=False):
                            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
                            for f in files:
                                f_path = os.path.join(root, f)
                                if os.path.islink(f_path):
                                    continue
                                _assert_within_base(f_path, base_dir, follow_symlinks=True)
                                arcname = os.path.relpath(f_path, work_dir)
                                zf.write(f_path, arcname)
                    elif os.path.isfile(item_full):
                        zf.write(item_full, os.path.relpath(item_full, work_dir))
        else:
            mode = "w:gz" if req.format in ("tar.gz", "tgz", "gz") else "w"
            with tarfile.open(target_dest, mode) as tf:
                for item_name in req.items:
                    item_full = resolve_safe_subitem(work_dir, item_name, base_dir)
                    if os.path.islink(item_full):
                        continue
                    if os.path.exists(item_full):
                        tf.add(item_full, arcname=os.path.relpath(item_full, work_dir))

        try:
            shutil.move(target_dest, archive_full_path)
            os.chmod(archive_full_path, 0o644)
        except PermissionError:
            subprocess.run(["sudo", "mv", target_dest, archive_full_path], check=True)
            subprocess.run(["sudo", "chmod", "644", archive_full_path], check=False)

        sync_owner(archive_full_path)
        return {"status": "ok", "message": f"Nén thành công tệp {archive_name}!", "archive_name": archive_name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi nén tệp: {str(e)}")
    finally:
        if os.path.exists(target_dest):
            os.remove(target_dest)


@router.post("/extract")
async def extract_archive(
    req: ExtractRequest,
    current_user: dict = Depends(get_current_user),
):
    """Giải nén an toàn (.zip, .tar, .tar.gz, .tgz) có chống Zip Slip, Symlink Escape & Zip Bomb."""
    base_dir, work_dir = await resolve_base_and_target(current_user, req.target_user, req.current_path)
    archive_path = resolve_safe_subitem(work_dir, req.name, base_dir)
    if not os.path.isfile(archive_path):
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp nén để giải nén.")

    extract_dir = work_dir
    if req.dest_subdir and req.dest_subdir.strip():
        sub_clean = _validate_single_name(req.dest_subdir.strip(), "Thư mục giải nén")
        extract_dir = resolve_safe_child(work_dir, sub_clean, base_dir)
        os.makedirs(extract_dir, exist_ok=True)
    real_extract = os.path.realpath(extract_dir)
    _assert_within_base(real_extract, base_dir, follow_symlinks=True)

    low = archive_path.lower()
    extracted_count = 0
    total_size = 0

    try:
        if low.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                infos = zf.infolist()
                if len(infos) > MAX_EXTRACT_ENTRIES:
                    raise HTTPException(status_code=400, detail=f"File nén chứa quá nhiều mục (> {MAX_EXTRACT_ENTRIES}).")
                for zi in infos:
                    if zi.filename.startswith("/") or "\\" in zi.filename.replace("\\", "/").split("/")[0:1]:
                        pass
                    norm = zi.filename.replace("\\", "/").lstrip("/")
                    if not norm or any(seg in ("", ".", "..") for seg in norm.split("/") if seg):
                        if any(seg == ".." for seg in norm.split("/")):
                            raise HTTPException(status_code=403, detail=f"Phát hiện Zip Slip nguy hiểm trong file nén: {zi.filename}")
                    # Bỏ qua symlink bên trong zip (Unix mode 0120000)
                    unix_mode = (zi.external_attr >> 16) & 0o170000
                    if unix_mode == 0o120000:
                        continue

                    target_m = os.path.abspath(os.path.join(real_extract, norm))
                    try:
                        Path(target_m).relative_to(Path(real_extract))
                    except ValueError:
                        raise HTTPException(status_code=403, detail=f"Phát hiện Zip Slip vượt thư mục đích: {zi.filename}")

                    if zi.is_dir():
                        os.makedirs(target_m, exist_ok=True)
                    else:
                        total_size += zi.file_size
                        if total_size > MAX_EXTRACT_BYTES:
                            raise HTTPException(status_code=413, detail="Tổng dung lượng giải nén vượt quá giới hạn an toàn 1 GB (Zip Bomb Guard).")
                        os.makedirs(os.path.dirname(target_m), exist_ok=True)
                        with zf.open(zi, "r") as src_f, open(target_m, "wb") as out_f:
                            shutil.copyfileobj(src_f, out_f)
                        os.chmod(target_m, 0o644)
                        extracted_count += 1

        elif low.endswith((".tar.gz", ".tgz", ".tar")):
            tmode = "r:gz" if low.endswith((".tar.gz", ".tgz")) else "r:"
            with tarfile.open(archive_path, tmode) as tf:
                members = tf.getmembers()
                if len(members) > MAX_EXTRACT_ENTRIES:
                    raise HTTPException(status_code=400, detail=f"File nén chứa quá nhiều mục (> {MAX_EXTRACT_ENTRIES}).")
                for m in members:
                    if m.issym() or m.islnk() or m.isdev() or m.ischr() or m.isblk() or m.isfifo():
                        continue
                    norm = m.name.replace("\\", "/").lstrip("./")
                    if not norm or any(seg == ".." for seg in norm.split("/")):
                        raise HTTPException(status_code=403, detail=f"Phát hiện đường dẫn nguy hiểm trong file tar: {m.name}")
                    target_m = os.path.abspath(os.path.join(real_extract, norm))
                    try:
                        Path(target_m).relative_to(Path(real_extract))
                    except ValueError:
                        raise HTTPException(status_code=403, detail=f"Phát hiện Tar Slip vượt thư mục đích: {m.name}")

                    if m.isdir():
                        os.makedirs(target_m, exist_ok=True)
                    elif m.isfile():
                        total_size += m.size
                        if total_size > MAX_EXTRACT_BYTES:
                            raise HTTPException(status_code=413, detail="Tổng dung lượng giải nén vượt quá giới hạn an toàn 1 GB.")
                        os.makedirs(os.path.dirname(target_m), exist_ok=True)
                        fobj = tf.extractfile(m)
                        if fobj:
                            with fobj, open(target_m, "wb") as out_f:
                                shutil.copyfileobj(fobj, out_f)
                            os.chmod(target_m, 0o644)
                            extracted_count += 1
        else:
            raise HTTPException(status_code=400, detail="Chỉ hỗ trợ giải nén định dạng .zip, .tar.gz, .tgz và .tar.")

        sync_owner(real_extract)
        return {
            "status": "ok",
            "message": f"Đã giải nén thành công {extracted_count} tệp tin ({format_bytes(total_size)})!",
            "count": extracted_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi giải nén: {str(e)}")


@router.post("/search-content")
async def search_file_content(
    req: SearchContentRequest,
    current_user: dict = Depends(get_current_user),
):
    _, work_dir = await resolve_base_and_target(current_user, req.target_user, req.current_path)
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=404, detail="Thư mục làm việc không tồn tại.")

    query = req.query.strip()
    if not query:
        return {"items": [], "count": 0}

    results = []
    max_results = 80

    # 1. grep trên Linux với "--" chống Argument Injection
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
            "--",
            query,
            ".",
        ])
        proc = subprocess.run(cmd, cwd=work_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        if proc.returncode in (0, 1) and proc.stdout:
            for line in proc.stdout.strip().split("\n"):
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
                    results.append({"file": fpath, "line": lineno, "snippet": snippet})
                    if len(results) >= max_results:
                        break
            if results:
                return {"items": results, "count": len(results)}
    except Exception:
        pass

    # 2. Fallback Python os.walk
    count = 0
    flags = 0 if req.case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(query), flags)
    binary_exts = (".zip", ".gz", ".tar", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".exe", ".bin", ".woff", ".woff2", ".ttf")

    for root, dirs, files in os.walk(work_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_WALK_DIRS]
        for f in files:
            if os.path.splitext(f)[1].lower() in binary_exts:
                continue
            fpath = os.path.join(root, f)
            try:
                if os.path.islink(fpath) or os.path.getsize(fpath) > 2 * 1024 * 1024:
                    continue
                rel_fpath = os.path.relpath(fpath, work_dir).replace("\\", "/")
                with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                    for idx, line in enumerate(fh, 1):
                        if pattern.search(line):
                            snip = line.strip()
                            if len(snip) > 200:
                                snip = snip[:200] + "..."
                            results.append({"file": rel_fpath, "line": idx, "snippet": snip})
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
