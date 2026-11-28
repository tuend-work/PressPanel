#!/usr/bin/env python3
"""
PressPanel MySQL Governor
Giám sát và kiểm soát tài nguyên MySQL/MariaDB theo từng user:
- Ngắt các truy vấn chạy quá lâu (max_query_time, mặc định 30s)
- Giới hạn tổng thời gian truy vấn trong cửa sổ 60s (max_cpu_sec_per_min, mặc định 120s)
- Giới hạn số truy vấn đồng thời (max_concurrent_queries, mặc định 20)
"""
import argparse
import json
import logging
import os
import subprocess
import time
from collections import defaultdict

LOG_PATH = "/var/log/presspanel-mysql-governor.log"
CONF_PATH = "/etc/presspanel/mysql_governor.json"

DEFAULT_CONF = {
    "interval_sec": 3,
    "max_query_time_sec": 30,
    "max_concurrent_queries": 20,
    "max_cpu_sec_per_min": 120,
    "ignored_users": ["root", "mariadb.sys", "mysql", "debian-sys-maint", "pma", "phpmyadminsys", "presspanel", "system user"],
}

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def load_config() -> dict:
    conf = dict(DEFAULT_CONF)
    if os.path.exists(CONF_PATH):
        try:
            with open(CONF_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                conf.update(data)
        except Exception:
            pass
    return conf


def run_mysql_query(sql: str) -> str:
    res = subprocess.run(
        ["mysql", "--batch", "-N", "-e", sql],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=5,
        check=False,
    )
    if res.returncode != 0:
        return ""
    return res.stdout


def main():
    parser = argparse.ArgumentParser(description="PressPanel MySQL Governor")
    parser.add_argument("--once", action="store_true", help="Run a single inspection cycle and exit")
    args = parser.parse_args()

    # Lưu lịch sử thời gian chạy query tích lũy trong 60s gần nhất: {user: [(ts, active_sec), ...]}
    usage_history = defaultdict(list)

    while True:
        conf = load_config()
        interval = max(1, int(conf.get("interval_sec", 3)))
        max_q_time = max(5, int(conf.get("max_query_time_sec", 30)))
        max_conc = max(3, int(conf.get("max_concurrent_queries", 20)))
        max_cpu_min = max(15, int(conf.get("max_cpu_sec_per_min", 120)))
        ignored = set(conf.get("ignored_users", []))

        now = time.time()
        raw = run_mysql_query("SHOW FULL PROCESSLIST;")
        user_procs = defaultdict(list)

        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            pid_s, user, _host, _db, command, time_s = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
            if user in ignored or command in ("Sleep", "Binlog Dump", "Daemon"):
                continue
            try:
                pid = int(pid_s)
                qtime = int(time_s)
            except ValueError:
                continue
            user_procs[user].append((pid, qtime, command))

        for user, procs in user_procs.items():
            # Cộng dồn thời gian active trong chu kỳ này
            usage_history[user] = [(t, v) for (t, v) in usage_history[user] if now - t <= 60.0]
            usage_history[user].append((now, len(procs) * interval))
            total_sec_60s = sum(v for _, v in usage_history[user])

            # Sắp xếp query chạy lâu nhất lên trước
            procs.sort(key=lambda x: x[1], reverse=True)

            # 1. Kill query vượt max_query_time_sec
            for pid, qtime, cmd in procs:
                if qtime >= max_q_time:
                    run_mysql_query(f"KILL QUERY {pid};")
                    logging.warning(f"Killed long query pid={pid} user={user} time={qtime}s cmd={cmd}")

            # 2. Nếu vượt ngưỡng đồng thời hoặc vượt tổng CPU-time 60s -> throttle bằng cách ngắt query nặng nhất
            if len(procs) > max_conc or total_sec_60s > max_cpu_min:
                over = max(1, len(procs) - max_conc)
                for pid, qtime, cmd in procs[:over]:
                    if qtime >= 2:
                        run_mysql_query(f"KILL QUERY {pid};")
                        logging.warning(
                            f"Throttled user={user} pid={pid} conc={len(procs)}/{max_conc} cpu_60s={total_sec_60s}s/{max_cpu_min}s"
                        )

        if args.once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
