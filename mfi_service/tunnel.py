from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time


LOG = logging.getLogger("nas-mfi-tunnel")
STOP = threading.Event()


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"缺少环境变量 {name}")
    return value


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("MFI_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = required("RELAY_SSH_HOST")
    user = required("RELAY_SSH_USER")
    remote_port = int(required("RELAY_REMOTE_PORT"))
    ssh_port = int(os.environ.get("RELAY_SSH_PORT", "22"))
    local_port = int(os.environ.get("MFI_BIND_PORT", "8787"))
    key_path = os.environ.get("RELAY_SSH_KEY_PATH", "/etc/nas-mfi/relay_ssh_key")
    known_hosts = os.environ.get("RELAY_KNOWN_HOSTS_PATH", "/etc/nas-mfi/relay_known_hosts")
    if not 1 <= remote_port <= 65535 or not 1 <= ssh_port <= 65535:
        raise SystemExit("SSH 或反向隧道端口无效")
    for path in (key_path, known_hosts):
        if not os.path.isfile(path):
            raise SystemExit(f"缺少文件：{path}")

    current: subprocess.Popen | None = None

    def stop(_signum, _frame) -> None:
        STOP.set()
        if current is not None:
            current.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    delay = 1
    while not STOP.is_set():
        command = [
            "/usr/bin/ssh",
            "-N",
            "-T",
            "-i",
            key_path,
            "-p",
            str(ssh_port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=20",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-R",
            f"127.0.0.1:{remote_port}:127.0.0.1:{local_port}",
            f"{user}@{host}",
        ]
        LOG.info("正在连接中转服务器 %s:%d，回环端口 %d", host, ssh_port, remote_port)
        started = time.monotonic()
        current = subprocess.Popen(command)
        return_code = current.wait()
        current = None
        if STOP.is_set():
            break
        runtime = time.monotonic() - started
        LOG.warning("SSH 隧道退出，code=%d，%d 秒后重连", return_code, delay)
        STOP.wait(delay)
        delay = 1 if runtime >= 60 else min(delay * 2, 30)


if __name__ == "__main__":
    main()
