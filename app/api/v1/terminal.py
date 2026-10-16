import asyncio
import os
import pty
import select
import fcntl
import termios
import struct
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, status
from app.core.security import decode_token
from app.db.session import get_db_connection

router = APIRouter(prefix="/terminal", tags=["SSH Web Terminal"])

@router.websocket("/ws")
async def terminal_websocket(
    websocket: WebSocket,
    token: str = Query(...)
):
    """
    WebSocket Terminal giả lập PTY an toàn cho từng Hosting User / Web User.
    - Token bắt buộc phải hợp lệ
    - Nếu là Admin: Spawn bash dưới user root (hoặc switch user theo chỉ định)
    - Nếu là Hosting User / Web User:
        + Spawn PTY và lập tức chuyển đổi UID/GID hoặc su/runuser sang đúng Linux User của họ
        + Thư mục khởi đầu: /home/<username>
        + Áp trần Cgroups và quyền hạn tệp tin chặt chẽ của Linux
    """
    await websocket.accept()

    # 1. Xác thực Token
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        await websocket.send_text("\r\n\x1b[31m[Lỗi] Token xác thực không hợp lệ hoặc đã hết hạn!\x1b[0m\r\n")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user_id = int(payload["sub"])
    db = await get_db_connection()
    cursor = await db.execute("""
        SELECT u.id, u.username, u.role, u.status, u.package_id, COALESCE(p.allow_ssh, 1) as allow_ssh
        FROM users u
        LEFT JOIN packages p ON u.package_id = p.id
        WHERE u.id = ?
    """, (user_id,))
    user = await cursor.fetchone()

    if not user or user["status"] == "suspended":
        await db.close()
        await websocket.send_text("\r\n\x1b[31m[Lỗi] Tài khoản không tồn tại hoặc đã bị tạm khóa!\x1b[0m\r\n")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Xác định Linux target user & working directory
    target_username = user["username"]
    is_admin = (user["role"] == "admin")

    # Kiểm tra quyền SSH cấp gói (Plan) nếu không phải admin
    if not is_admin and not bool(user["allow_ssh"]):
        await db.close()
        await websocket.send_text("\r\n\x1b[31m[Lỗi] Gói dịch vụ của bạn không có quyền truy cập Terminal SSH! Vui lòng nâng cấp gói.\x1b[0m\r\n")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Nếu token đại diện cho Web User của 1 website cụ thể
    if payload.get("web_domain_id"):
        d_cursor = await db.execute("SELECT allow_ssh, domain_name FROM domains WHERE id = ?", (payload["web_domain_id"],))
        dom = await d_cursor.fetchone()
        if dom and not bool(dom["allow_ssh"]) and not is_admin:
            await db.close()
            await websocket.send_text(f"\r\n\x1b[31m[Lỗi] Quyền Terminal SSH của website '{dom['domain_name']}' đang bị tắt!\x1b[0m\r\n")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    await db.close()

    if payload.get("role") == "user" and payload.get("web_user_name"):
        target_username = payload["web_user_name"]

    home_dir = f"/home/{target_username}"
    if is_admin and not payload.get("web_user_name"):
        home_dir = "/root"

    # Mở PTY (Pseudo-Terminal)
    try:
        master_fd, slave_fd = pty.openpty()
    except Exception as e:
        await websocket.send_text(f"\r\n\x1b[31m[Lỗi] Không thể khởi tạo Terminal PTY: {str(e)}\x1b[0m\r\n")
        await websocket.close()
        return

    # Đặt non-blocking cho master_fd
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    # Khởi động Shell process:
    # Nếu đang chạy dưới quyền root (chế độ server chuẩn):
    # - Admin: chạy /bin/bash
    # - User: chạy qua su/runuser chuyển sang user đó
    # Nếu process panel chạy dưới user non-root (wpc) và cùng target_username:
    # - Chạy trực tiếp /bin/bash của user
    # - Ngược lại: dùng sudo -u target_username /bin/bash hoặc su
    if is_admin and not payload.get("web_user_name"):
        shell_cmd = ["/bin/bash", "--login"]
    elif os.getuid() == 0:
        # Chạy trực tiếp dưới quyền root chuyển sang user
        if os.path.exists("/sbin/runuser"):
            shell_cmd = ["/sbin/runuser", "-u", target_username, "--", "/bin/bash", "--login"]
        else:
            shell_cmd = ["/bin/su", "-", target_username]
    else:
        # Nếu panel chạy dưới user unprivileged
        current_sys_user = os.environ.get("USER", "")
        if current_sys_user == target_username:
            shell_cmd = ["/bin/bash", "--login"]
        else:
            shell_cmd = ["sudo", "-n", "-u", target_username, "/bin/bash", "--login"]

    env = {
        "TERM": "xterm-256color",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": home_dir,
        "USER": target_username,
        "LANG": "en_US.UTF-8"
    }

    try:
        proc = await asyncio.create_subprocess_exec(
            *shell_cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=home_dir if os.path.exists(home_dir) else "/",
            env=env,
            close_fds=True
        )
    except Exception as e:
        # Thử fallback chạy bash trực tiếp
        try:
            fallback_cmd = ["/bin/bash", "--login"]
            proc = await asyncio.create_subprocess_exec(
                *fallback_cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=home_dir if os.path.exists(home_dir) else "/",
                env=env,
                close_fds=True
            )
        except Exception as fe:
            os.close(slave_fd)
            os.close(master_fd)
            await websocket.send_text(f"\r\n\x1b[31m[Lỗi] Không thể khởi động Shell: {str(fe)}\x1b[0m\r\n")
            await websocket.close()
            return

    os.close(slave_fd)

    loop = asyncio.get_running_loop()
    closed = False

    async def pty_reader():
        nonlocal closed
        while not closed and proc.returncode is None:
            try:
                # Đọc output từ PTY
                data = await loop.run_in_executor(None, _read_fd, master_fd)
                if data:
                    await websocket.send_text(data.decode("utf-8", errors="replace"))
                else:
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
                    import json
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
