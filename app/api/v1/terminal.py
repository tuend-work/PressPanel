import asyncio
import os
import pty
import select
import fcntl
import termios
import struct
import secrets
import time
import json
import re
from typing import Dict, Any, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends, HTTPException, status
from app.core.security import decode_token
from app.api.deps import get_current_user
from app.db.session import get_db_connection

router = APIRouter(prefix="/terminal", tags=["SSH Web Terminal"])

@router.post("/ticket")
async def create_terminal_ticket(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin" and not current_user.get("web_domain_id"):
        raise HTTPException(status_code=403, detail="Tài khoản Hosting User không có quyền sử dụng Web Terminal SSH.")
    now = time.time()
    ticket = secrets.token_urlsafe(24)
    db = await get_db_connection()
    try:
        await db.execute("CREATE TABLE IF NOT EXISTS terminal_tickets (ticket TEXT PRIMARY KEY, user_json TEXT, exp REAL)")
        await db.execute("DELETE FROM terminal_tickets WHERE exp < ?", (now,))
        await db.execute(
            "INSERT INTO terminal_tickets (ticket, user_json, exp) VALUES (?, ?, ?)",
            (ticket, json.dumps(current_user), now + 30.0)
        )
        await db.commit()
    finally:
        await db.close()
    return {"ticket": ticket, "expires_in": 30}

@router.websocket("/ws")
async def terminal_websocket(
    websocket: WebSocket,
    ticket: Optional[str] = Query(None),
    token: Optional[str] = Query(None),
    target_user: Optional[str] = Query(None)
):
    """
    WebSocket Terminal giả lập PTY an toàn:
    - Admin: Spawn bash dưới user root (hoặc caged application user nếu chỉ định)
    - Application: Spawn caged shell dưới danh tính Linux của chính website đó
    - User: Tắt hoàn toàn tính năng Terminal SSH (chỉ quản lý trên Panel)
    """
    await websocket.accept()

    user_id = None
    is_admin = False
    is_web_user = False
    web_domain_id = None
    web_user_name = None

    if ticket:
        t_row = None
        db_t = await get_db_connection()
        try:
            await db_t.execute("CREATE TABLE IF NOT EXISTS terminal_tickets (ticket TEXT PRIMARY KEY, user_json TEXT, exp REAL)")
            cur_t = await db_t.execute("SELECT user_json, exp FROM terminal_tickets WHERE ticket = ?", (ticket,))
            t_row = await cur_t.fetchone()
            if t_row:
                await db_t.execute("DELETE FROM terminal_tickets WHERE ticket = ?", (ticket,))
                await db_t.commit()
        finally:
            await db_t.close()

        if not t_row:
            await websocket.send_text("\r\n\x1b[31m[Lỗi] Vé phiên Terminal không hợp lệ hoặc đã sử dụng!\x1b[0m\r\n")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        if time.time() <= float(t_row["exp"]):
            u_obj = json.loads(t_row["user_json"])
            user_id = u_obj["id"]
            is_admin = (u_obj.get("role") == "admin" or u_obj.get("username") == "admin")
            is_web_user = (u_obj.get("role") == "application" or bool(u_obj.get("web_domain_id")))
            web_domain_id = u_obj.get("web_domain_id")
            web_user_name = u_obj.get("web_user_name") or u_obj.get("username")
        else:
            await websocket.send_text("\r\n\x1b[31m[Lỗi] Vé phiên Terminal đã hết hạn!\x1b[0m\r\n")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    elif token:
        payload = decode_token(token)
        if not payload or "sub" not in payload:
            await websocket.send_text("\r\n\x1b[31m[Lỗi] Token xác thực không hợp lệ hoặc đã hết hạn!\x1b[0m\r\n")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        user_id = int(payload["sub"])
        db = await get_db_connection()
        cursor = await db.execute("SELECT id, username, role, status FROM users WHERE id = ?", (user_id,))
        user = await cursor.fetchone()
        await db.close()
        if not user or user["status"] == "suspended":
            await websocket.send_text("\r\n\x1b[31m[Lỗi] Tài khoản không tồn tại hoặc đã bị tạm khóa!\x1b[0m\r\n")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        is_admin = (user["role"] == "admin" or user["username"] == "admin")
        is_web_user = (payload.get("role") == "application" or bool(payload.get("web_domain_id")))
        web_domain_id = payload.get("web_domain_id")
        web_user_name = payload.get("web_user_name") or user["username"]
    else:
        await websocket.send_text("\r\n\x1b[31m[Lỗi] Thiếu vé xác thực Terminal!\x1b[0m\r\n")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Xác định user mục tiêu trên Linux
    db = await get_db_connection()
    if is_web_user:
        # Website User / Application: Kết nối bằng quyền Linux user của website trong Cage cách ly
        if web_domain_id and not web_user_name:
            cur_dom = await db.execute("SELECT domain_name FROM domains WHERE id = ?", (web_domain_id,))
            d_row = await cur_dom.fetchone()
            if d_row and d_row["domain_name"]:
                web_user_name = re.sub(r'[^a-zA-Z0-9]', '', str(d_row["domain_name"])).lower()
        target_username = web_user_name
    elif is_admin:
        if target_user and target_user != "root":
            t_clean = target_user.strip()
            # Kiểm tra xem target_user có phải là domain_name không
            cursor = await db.execute("""
                SELECT u.username 
                FROM domains d 
                JOIN users u ON d.user_id = u.id 
                WHERE d.domain_name = ?
            """, (t_clean,))
            d_row = await cursor.fetchone()
            if d_row and d_row["username"]:
                target_username = d_row["username"]
            else:
                cursor = await db.execute("SELECT username FROM users WHERE username = ?", (t_clean,))
                u_row = await cursor.fetchone()
                if u_row and u_row["username"]:
                    target_username = u_row["username"]
                else:
                    target_username = t_clean
        else:
            target_username = "root"
    else:
        await db.close()
        # Hosting User: Tắt tính năng, tắt quyền SSH
        await websocket.send_text("\r\n\x1b[31m[Chính sách bảo mật] Tài khoản Hosting User bị tắt tính năng Terminal SSH. Chỉ Quản trị viên (Admin) và Tài khoản Website User mới có quyền truy cập Terminal.\x1b[0m\r\n")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await db.close()

    # Tìm đường dẫn script cage_terminal
    cage_script = "/opt/presspanel/scripts/cage_terminal.sh" if os.path.exists("/opt/presspanel/scripts/cage_terminal.sh") else "/opt/presspanel/scripts/cage_terminal.sh"
    if not os.path.exists(cage_script):
        # Thử đường dẫn trong workspace dev
        dev_cage = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "scripts", "cage_terminal.sh")
        if os.path.exists(dev_cage):
            cage_script = dev_cage

    if target_username == "root":
        home_dir = "/root" if os.path.exists("/root") else "/"
        if os.getuid() == 0:
            shell_cmd = ["/bin/bash", "--login"]
        else:
            shell_cmd = ["sudo", "-n", "-u", "root", "/bin/bash", "--login"]
    else:
        home_dir = f"/home/{target_username}" if os.path.exists(f"/home/{target_username}") else "/"
        if os.path.exists(cage_script):
            if os.getuid() == 0:
                shell_cmd = [cage_script, target_username]
            else:
                shell_cmd = ["sudo", "-n", cage_script, target_username]
        else:
            # Fallback trực tiếp nếu không có cage_script
            if os.getuid() == 0:
                shell_cmd = ["su", "-s", "/bin/bash", "-", target_username]
            else:
                shell_cmd = ["sudo", "-n", "-u", target_username, "/bin/bash", "--login"]

    # Mở PTY (Pseudo-Terminal)
    try:
        master_fd, slave_fd = pty.openpty()
        # Đảm bảo slave_fd chỉ có quyền đọc/ghi cho chủ sở hữu (0o600)
        try:
            slave_name = os.ttyname(slave_fd)
            os.chmod(slave_name, 0o600)
        except Exception:
            pass
    except Exception as e:
        await websocket.send_text(f"\r\n\x1b[31m[Lỗi] Không thể khởi tạo Terminal PTY: {str(e)}\x1b[0m\r\n")
        await websocket.close()
        return

    # Đặt non-blocking cho master_fd
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    env = {
        "TERM": "xterm-256color",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": home_dir,
        "USER": target_username,
        "LOGNAME": target_username,
        "SHELL": "/bin/bash",
        "LANG": "en_US.UTF-8"
    }

    def _setup_child_terminal(fd):
        def _preexec():
            # Tạo process group / session mới và gắn slave PTY làm controlling tty
            os.setsid()
            try:
                import fcntl
                import termios
                fcntl.ioctl(fd, termios.TIOCSCTTY, 0)
            except Exception:
                pass
        return _preexec

    try:
        proc = await asyncio.create_subprocess_exec(
            *shell_cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=home_dir if os.path.exists(home_dir) else "/",
            env=env,
            preexec_fn=_setup_child_terminal(slave_fd),
            close_fds=True
        )
    except Exception as fe:
        os.close(slave_fd)
        os.close(master_fd)
        await websocket.send_text(f"\r\n\x1b[31m[Lỗi] Không thể khởi động Shell cách ly: {str(fe)}\x1b[0m\r\n")
        await websocket.close()
        return

    os.close(slave_fd)

    loop = asyncio.get_running_loop()
    closed = False

    async def pty_reader():
        nonlocal closed
        empty_count = 0
        while not closed:
            try:
                # Đọc output từ PTY
                data = await loop.run_in_executor(None, _read_fd, master_fd)
                if data:
                    await websocket.send_text(data.decode("utf-8", errors="replace"))
                    empty_count = 0
                else:
                    empty_count += 1
                    if proc.returncode is not None and empty_count > 5:
                        break
                    await asyncio.sleep(0.02)
            except Exception:
                break
        closed = True

    reader_task = asyncio.create_task(pty_reader())

    try:
        while not closed and proc.returncode is None:
            msg = await websocket.receive_text()
            # Kiểm tra xem có phải lệnh resize window không: json format {"resize": [cols, rows]}
            if msg.startswith('{"resize":') and msg.endswith('}'):
                try:
                    rdata = json.loads(msg)
                    cols, rows = rdata["resize"]
                    # ioctl set window size
                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                    continue
                except Exception:
                    pass
            
            # Ghi dữ liệu phím bấm của người dùng vào PTY
            os.write(master_fd, msg.encode("utf-8"))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:
        pass
    finally:
        closed = True
        reader_task.cancel()
        try:
            os.close(master_fd)
        except Exception:
            pass
        if proc.returncode is None:
            try:
                proc.terminate()
            except Exception:
                pass

def _read_fd(fd: int) -> bytes:
    try:
        r, _, _ = select.select([fd], [], [], 0.05)
        if fd in r:
            return os.read(fd, 4096)
    except Exception:
        return b""
    return b""
