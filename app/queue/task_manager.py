import asyncio
import os
import uuid
from datetime import datetime
from typing import Dict, Set
from fastapi import WebSocket
from app.core.config import settings
from app.db.session import get_db_connection

class TaskManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, task_id: str, websocket: WebSocket):
        await websocket.accept()
        if task_id not in self.active_connections:
            self.active_connections[task_id] = set()
        self.active_connections[task_id].add(websocket)

    def disconnect(self, task_id: str, websocket: WebSocket):
        if task_id in self.active_connections:
            self.active_connections[task_id].discard(websocket)
            if not self.active_connections[task_id]:
                del self.active_connections[task_id]

    async def broadcast_log(self, task_id: str, line: str):
        if task_id in self.active_connections:
            for connection in list(self.active_connections[task_id]):
                try:
                    await connection.send_text(line)
                except Exception:
                    self.disconnect(task_id, connection)

    async def run_task(self, user_id: int, action: str, coro_func, *args, **kwargs) -> str:
        task_id = str(uuid.uuid4())
        log_dir = os.path.join(settings.LOGS_DIR, "tasks")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"{task_id}.log")

        db = await get_db_connection()
        await db.execute("""
            INSERT INTO tasks (id, user_id, action, status, log_file)
            VALUES (?, ?, ?, 'running', ?)
        """, (task_id, user_id, action, log_file))
        await db.commit()
        await db.close()

        async def _worker():
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] Bat dau tac vu: {action}\n")
                try:
                    await coro_func(task_id, f, *args, **kwargs)
                    status = "completed"
                    f.write(f"[{datetime.now().isoformat()}] Tac vu hoan tat thanh cong!\n")
                except Exception as e:
                    status = "failed"
                    f.write(f"[{datetime.now().isoformat()}] Loi tac vu: {str(e)}\n")

            db = await get_db_connection()
            await db.execute("""
                UPDATE tasks SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?
            """, (status, task_id))
            await db.commit()
            await db.close()

        asyncio.create_task(_worker())
        return task_id

task_manager = TaskManager()
