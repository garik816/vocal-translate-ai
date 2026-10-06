from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .common import ACE_DIR, LOG_DIR, ROOT, ace_python, child_env, log, require, tail_text


def post_json(url: str, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_multipart(url: str, fields: dict[str, str],
                   files: dict[str, tuple[str, Path, str]], timeout: int = 180) -> dict[str, Any]:
    boundary = "----vta" + uuid.uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(str(value).encode("utf-8") + b"\r\n")
    for field, (name, path, mime) in files.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{field}"; filename="{name}"\r\n'.encode("utf-8"))
        body.extend(f"Content-Type: {mime}\r\n\r\n".encode())
        body.extend(path.read_bytes() + b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        url, data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def download(url: str, output: Path, timeout: int = 180) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=timeout) as response, output.open("wb") as fh:
        shutil.copyfileobj(response, fh, length=1024 * 1024)


def health_ok(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=2) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def start_ace_server(port: int) -> tuple[subprocess.Popen[Any] | None, bool]:
    base_url = f"http://127.0.0.1:{port}"
    if health_ok(base_url):
        log("[ACE] Existing API server detected.")
        return None, False
    runner = ROOT / "scripts" / "ace_server_runner.py"
    require(runner, "ACE server runner")
    ace_log = LOG_DIR / "ace_api.log"
    handle = ace_log.open("a", encoding="utf-8", errors="replace", buffering=1)
    env = child_env()
    env["ACESTEP_INIT_LLM"] = "false"
    env["ACESTEP_CONFIG_PATH"] = "acestep-v15-turbo"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    log("[ACE] Starting API directly from existing venv (no uv sync)...")
    proc = subprocess.Popen(
        [str(ace_python()), str(runner), "--port", str(port)], cwd=str(ACE_DIR),
        env=env, stdout=handle, stderr=subprocess.STDOUT, creationflags=flags
    )
    deadline = time.time() + 240
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"ACE API exited with {proc.returncode}.\n{tail_text(ace_log, 50)}")
        if health_ok(base_url):
            log("[ACE] API ready.")
            return proc, True
        time.sleep(2)
    raise TimeoutError("ACE API did not become ready. See logs/ace_api.log")


def stop_process_tree(proc: subprocess.Popen[Any] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            proc.terminate()
            proc.wait(timeout=10)
    except Exception:
        pass
