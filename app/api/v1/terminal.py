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
    token: str = Query(...),
    target_user: str = Query(None)
):
    """
    WebSocket Terminal giả lập PTY an toàn cho từng Hosting User / Web User.
    - Token bắt buộc phải hợp lệ
    - Nếu là Admin: Spawn bash dưới user root (hoặc caged website user nếu chỉ định)
    - Nếu là Website User: Spawn caged shell dưới danh tính Linux của chính website đó (/opt/wpc/scripts/cage_terminal.sh)
    - Nếu là Hosting User: Tắt hoàn toàn tính năng Terminal SSH vì lý do bảo mật
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
        SELECT u.id, u.username, u.role, u.status
        FROM users u
        WHERE u.id = ?
    """, (user_id,))
    user = await cursor.fetchone()
    await db.close()

    if not user or user["status"] == "suspended":
        await websocket.send_text("\r\n\x1b[31m[Lỗi] Tài khoản không tồn tại hoặc đã bị tạm khóa!\x1b[0m\r\n")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    is_admin = (user["role"] == "admin" or user["username"] == "admin")
    is_web_user = "web_domain_id" in payload and "web_user_name" in payload
    web_domain_id = payload.get("web_domain_id")
    web_user_name = payload.get("web_user_name")

    # Xác định user mục tiêu trên Linux
    if is_web_user:
        # Website User: Kết nối bằng quyền của chính mình trong Cage cách ly
        target_username = web_user_name
        home_dir = f"/home/{target_username}" if os.path.exists(f"/home/{target_username}") else "/"
        shell_cmd = ["sudo", "-n", "/opt/wpc/scripts/cage_terminal.sh", target_username]
    elif is_admin:
        if target_user and target_user != "root":
            # Admin mở terminal của một Website User cụ thể
            target_username = target_user.strip()
            home_dir = f"/home/{target_username}" if os.path.exists(f"/home/{target_username}") else "/"
            shell_cmd = ["sudo", "-n", "/opt/wpc/scripts/cage_terminal.sh", target_username]
        else:
            # Admin mở terminal quản trị hệ thống: Kết nối thẳng quyền ROOT
            target_username = "root"
            home_dir = "/root" if os.path.exists("/root") else "/"
            if os.getuid() == 0:
                shell_cmd = ["/bin/bash", "--login"]
            else:
                shell_cmd = ["sudo", "-n", "-u", "root", "/bin/bash", "--login"]
    else:
        # Hosting User: Tắt tính năng, tắt quyền SSH
        await websocket.send_text("\r\n\x1b[31m[Chính sách bảo mật] Tài khoản Hosting User bị tắt tính năng Terminal SSH. Chỉ Quản trị viên (Admin) và Tài khoản Website User mới có quyền truy cập Terminal.\x1b[0m\r\n")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Mở PTY (Pseudo-Terminal)
    try:
        master_fd, slave_fd = pty.openpty()
        # Đảm bảo slave_fd có quyền đọc/ghi cho process con bất kể user nào
        try:
            slave_name = os.ttyname(slave_fd)
            os.chmod(slave_name, 0o666)
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
    except Exception as e:
        # Fallback chạy bash trực tiếp
        try:
            fallback_cmd = ["/bin/bash", "--login"]
            proc = await asyncio.create_subprocess_exec(
                *fallback_cmd,
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
