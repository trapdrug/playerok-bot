"""Diagnose the Railway container (processes, memory, queues)."""

from __future__ import annotations

import os
import sqlite3
import subprocess

print("--- процессы ---")
result = subprocess.run(
    ["ps", "-eo", "pid,ppid,rss,etimes,comm,args"],
    capture_output=True,
    text=True,
)
lines = result.stdout.splitlines()
chromium = [line for line in lines if "headless_shell" in line or "chrome" in line]
node = [line for line in lines if "playwright" in line]
print(f"всего строк ps: {len(lines)}")
print(f"процессов chromium: {len(chromium)}")
print(f"процессов playwright/node: {len(node)}")
for line in chromium[:6]:
    print("   ", line[:150])

print("--- лимиты ---")
for path in (
    "/sys/fs/cgroup/memory.max",
    "/sys/fs/cgroup/memory.current",
    "/sys/fs/cgroup/pids.max",
    "/sys/fs/cgroup/pids.current",
):
    try:
        with open(path) as handle:
            print(f"   {path}: {handle.read().strip()}")
    except Exception as exc:  # noqa: BLE001
        print(f"   {path}: {exc}")

print("--- потоки процесса бота ---")
try:
    with open(f"/proc/1/status") as handle:
        for line in handle:
            if line.startswith(("Threads", "VmRSS")):
                print("   ", line.strip())
except Exception as exc:  # noqa: BLE001
    print("   ", exc)

print("--- очередь бустов ---")
connection = sqlite3.connect("/data/playerok.db")
for row in connection.execute(
    "select id, sale_external_id, lot_url, status, attempts, reason "
    "from pending_boosts order by id desc limit 8"
):
    print("   ", row)
print("--- продажи (не baseline) ---")
for row in connection.execute(
    "select external_id, status, boost_state, title, price, lot_url, error "
    "from sales where status != 'baseline' order by id desc limit 10"
):
    print("   ", row)
print("--- последние действия ---")
for row in connection.execute(
    "select created_at, action, details from audit_log order by id desc limit 10"
):
    print("   ", row)
