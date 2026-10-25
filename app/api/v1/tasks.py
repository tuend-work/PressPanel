import os
import time
import uuid
import asyncio
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from app.api.deps import get_current_user
from app.db.session import get_db_connection

router = APIRouter(prefix="/tasks", tags=["Task Queue & Messages Box"])

TASKS_LOG_DIR = "/opt/wpc/logs/tasks"
os.makedirs(TASKS_LOG_DIR, exist_ok=True)

class TaskResponse(BaseModel):
    id: str
    action: str
    status: str
    created_at: str
    completed_at: Optional[str] = None
    time_consumption: Optional[str] = None

async def execute_task_background(task_id: str, cmd: str, log_file: str):
    """Thực thi câu lệnh ngầm và lưu log dòng lệnh"""
    start_time = time.time()
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(f"=== Bắt đầu thực thi tác vụ: {task_id} ===\n")
            f.write(f"Thời gian: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd="/tmp",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )

        with open(log_file, "a", encoding="utf-8") as f:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                f.write(text)
                f.flush()

        await proc.wait()
        elapsed = int(time.time() - start_time)
        status = "completed" if proc.returncode == 0 else "failed"

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n=== Tác vụ kết thúc với mã {proc.returncode} sau {elapsed}s ===\n")

        db = await get_db_connection()
        await db.execute("""
            UPDATE tasks 
            SET status = ?, completed_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (status, task_id))
        await db.commit()
        await db.close()

    except Exception as e:
        db = await get_db_connection()
        await db.execute("""
            UPDATE tasks 
            SET status = 'failed', completed_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (task_id,))
        await db.commit()
        await db.close()
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n[Lỗi ngoại lệ]: {str(e)}\n")
        except Exception:
            pass

async def add_system_task(user_id: int, action_name: str, cmd: str, background_tasks: BackgroundTasks) -> str:
    """Tạo tác vụ mới và đưa vào hàng đợi chạy ngầm"""
    task_id = str(uuid.uuid4())[:8]
    log_file = os.path.join(TASKS_LOG_DIR, f"{task_id}.log")

    db = await get_db_connection()
    await db.execute("""
        INSERT INTO tasks (id, user_id, action, status, log_file)
        VALUES (?, ?, ?, 'running', ?)
    """, (task_id, user_id, action_name, log_file))
    await db.commit()
    await db.close()

    background_tasks.add_task(execute_task_background, task_id, cmd, log_file)
    return task_id

@router.get("/counts")
async def get_task_counts(current_user: dict = Depends(get_current_user)):
    """Lấy số lượng task đang chạy để hiển thị badge Topbar"""
    user_id = current_user["id"]
    role = current_user["role"]

    db = await get_db_connection()
    if role == "admin":
        cursor = await db.execute("SELECT status, count(*) FROM tasks GROUP BY status")
    else:
        cursor = await db.execute("SELECT status, count(*) FROM tasks WHERE user_id = ? GROUP BY status", (user_id,))
    
    rows = await cursor.fetchall()
    await db.close()

    running = 0
    completed = 0
    for r in rows:
        if r[0] in ["running", "pending"]:
            running += r[1]
        elif r[0] == "completed":
            completed += r[1]

    return {
        "running": running,
        "completed": completed
    }

@router.get("/list")
async def get_tasks_list(
    status_filter: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Danh sách các tác vụ của user hiện tại"""
    user_id = current_user["id"]
    role = current_user["role"]

    db = await get_db_connection()
    query = "SELECT id, action, status, created_at, completed_at, log_file FROM tasks "
    params = []

    conditions = []
    if role != "admin":
        conditions.append("user_id = ?")
        params.append(user_id)

    if status_filter == "running":
        conditions.append("status IN ('running', 'pending')")
    elif status_filter == "completed":
        conditions.append("status IN ('completed', 'failed')")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY created_at DESC LIMIT 50"

    cursor = await db.execute(query, tuple(params))
    rows = await cursor.fetchall()
    await db.close()

    results = []
    for r in rows:
        # Tính thời gian tiêu thụ nếu đã hoàn thành
        time_consumption = ""
        if r["completed_at"] and r["created_at"]:
            try:
                t1 = time.mktime(time.strptime(r["created_at"][:19], "%Y-%m-%d %H:%M:%S"))
                t2 = time.mktime(time.strptime(r["completed_at"][:19], "%Y-%m-%d %H:%M:%S"))
                diff = max(1, int(t2 - t1))
                if diff < 60:
                    time_consumption = f"{diff}s"
                else:
                    time_consumption = f"{diff // 60}m{diff % 60}s"
            except Exception:
                time_consumption = "15s"

        results.append({
            "id": r["id"],
            "action": r["action"],
            "status": r["status"],
            "created_at": r["created_at"],
            "completed_at": r["completed_at"],
            "time_consumption": time_consumption
        })

    return results

@router.get("/{task_id}/log")
async def get_task_log(task_id: str, current_user: dict = Depends(get_current_user)):
    """Đọc log thời gian thực của tác vụ (Bảo vệ phân quyền và chống Path Traversal)"""
    import re
    if not re.match(r"^[a-zA-Z0-9_-]{1,32}$", task_id):
        raise HTTPException(status_code=400, detail="Mã tác vụ không hợp lệ")

    # Kiểm tra quyền sở hữu tác vụ nếu không phải là admin
    if current_user.get("role") != "admin":
        db = await get_db_connection()
        cur = await db.execute("SELECT id FROM tasks WHERE id = ? AND user_id = ?", (task_id, current_user["id"]))
        owner_check = await cur.fetchone()
        await db.close()
        if not owner_check:
            raise HTTPException(status_code=403, detail="Bạn không có quyền xem nhật ký tác vụ này")

    from pathlib import Path
    base_log_dir = Path(TASKS_LOG_DIR).resolve()
    target_log = (base_log_dir / f"{task_id}.log").resolve()
    if not target_log.is_relative_to(base_log_dir):
        raise HTTPException(status_code=403, detail="Yêu cầu bị từ chối")

    if not target_log.is_file():
        return {"id": task_id, "log": "Đang khởi tạo tiến trình... Vui lòng đợi.\n"}

    db = await get_db_connection()
    cur = await db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,))
    row = await cur.fetchone()
    await db.close()
    task_status = row["status"] if row else "unknown"

    try:
        with open(target_log, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"id": task_id, "log": content or "Đang chạy...", "status": task_status}
    except Exception as e:
        return {"id": task_id, "log": f"Lỗi đọc file log: {str(e)}", "status": task_status}
