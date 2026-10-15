import asyncio
import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("presspanel.executor")

async def run_gatekeeper(module: str, *args, stdin_data: Optional[str] = None) -> Dict[str, Any]:
    """
    Thực thi an toàn một trong 8 C gatekeeper tại /opt/presspanel/bin/<module>
    Truyền tham số và đọc dữ liệu JSON chuẩn từ stdout/stderr.
    """
    binary_path = f"/opt/presspanel/bin/{module}"
    cmd = [binary_path] + [str(a) for a in args]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_data else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate(
            input=stdin_data.encode("utf-8") if stdin_data else None
        )
        
        raw_output = stdout.decode("utf-8").strip() or stderr.decode("utf-8").strip()
        
        if not raw_output:
            return {"status": "error", "module": module, "message": f"Exit code {proc.returncode} voi output rong"}

        try:
            return json.loads(raw_output)
        except json.JSONDecodeError:
            return {
                "status": "error" if proc.returncode != 0 else "ok",
                "module": module,
                "message": raw_output
            }

    except FileNotFoundError:
        # Trong môi trường dev chưa cài SUID binary vào /opt/presspanel/bin
        logger.warning(f"Binary gatekeeper {binary_path} khong ton tai (dev mode).")
        return {
            "status": "dev_mock",
            "module": module,
            "cmd": " ".join(cmd),
            "message": "Gatekeeper binary khong ton tai tren moi truong dev nay"
        }
    except Exception as e:
        logger.error(f"Loi thuc thi gatekeeper {module}: {str(e)}")
        return {"status": "error", "module": module, "message": str(e)}
