# -*- coding: utf-8 -*-
"""Free TCP port before starting Web server (Windows/Linux)."""
import logging
import subprocess
import sys
import time

logger = logging.getLogger(__name__)


def _pids_on_port_windows(port: int) -> list[int]:
    pids = []
    result = subprocess.run(
        ['netstat', '-ano'],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='ignore',
        check=False,
    )
    token = f':{port}'
    for line in result.stdout.splitlines():
        if token not in line or 'LISTENING' not in line.upper():
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[-1])
            if pid > 0:
                pids.append(pid)
        except ValueError:
            continue
    return list(dict.fromkeys(pids))


def _kill_pid_windows(pid: int) -> bool:
    r = subprocess.run(
        ['taskkill', '/F', '/PID', str(pid)],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='ignore',
        check=False,
    )
    return r.returncode == 0


def ensure_port_free(port: int, label: str = 'fanqie-web') -> list[int]:
    """Terminate processes listening on *port*. Returns killed PIDs."""
    killed: list[int] = []
    if sys.platform == 'win32':
        for pid in _pids_on_port_windows(port):
            if _kill_pid_windows(pid):
                killed.append(pid)
                logger.info('[%s] 已结束占用端口 %s 的进程 PID=%s', label, port, pid)
        if killed:
            time.sleep(0.8)
        return killed

    # Linux / macOS
    try:
        result = subprocess.run(
            ['lsof', '-ti', f':{port}'],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line.isdigit():
                continue
            pid = int(line)
            subprocess.run(['kill', '-9', str(pid)], check=False)
            killed.append(pid)
            logger.info('[%s] 已结束占用端口 %s 的进程 PID=%s', label, port, pid)
        if killed:
            time.sleep(0.8)
    except FileNotFoundError:
        logger.warning('无法自动释放端口 %s：未找到 lsof', port)
    return killed
