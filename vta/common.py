from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
LOG_DIR = ROOT / "logs"
OUT_DIR = ROOT / "out"
WORK_DIR = ROOT / "work"
INPUT_DIR = ROOT / "input"
ACE_DIR = ROOT / "runtime" / "ACE-Step-1.5"
SEED_DIR = ROOT / "runtime" / "seed-vc"
RUN_LOG = LOG_DIR / "run.log"


def log(message: str = "") -> None:
    print(message, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def tail_text(path: Path, lines: int = 50) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(data[-lines:])
    except Exception:
        return ""


def run_logged(cmd: list[str], log_path: Path, cwd: Path | None = None,
               env: dict[str, str] | None = None, check: bool = True) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"$ {' '.join(cmd)}")
    with log_path.open("a", encoding="utf-8", errors="replace") as stage:
        stage.write("\n=== COMMAND ===\n" + " ".join(cmd) + "\n")
        stage.flush()
        proc = subprocess.Popen(
            cmd, cwd=str(cwd) if cwd else None, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line.rstrip("\r\n"), flush=True)
            stage.write(line)
            with RUN_LOG.open("a", encoding="utf-8") as main_log:
                main_log.write(line)
        rc = proc.wait()
        stage.write(f"\n=== EXIT CODE: {rc} ===\n")
    if check and rc != 0:
        raise RuntimeError(
            f"Command failed with exit code {rc}. See {log_path.relative_to(ROOT)}\n"
            + tail_text(log_path, 35)
        )
    return rc


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def require(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")


def ace_python() -> Path:
    path = ACE_DIR / ".venv" / "Scripts" / "python.exe"
    require(path, "ACE-Step Python")
    return path


def seed_python() -> Path:
    path = SEED_DIR / ".venv" / "Scripts" / "python.exe"
    require(path, "Seed-VC Python")
    return path


def find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH. Run SETUP_ONLY.bat.")
    return ffmpeg


def find_input_mp3() -> Path:
    files = sorted(INPUT_DIR.glob("*.mp3"))
    if not files:
        raise RuntimeError("No MP3 found in input/. Put exactly one original MP3 there.")
    if len(files) > 1:
        raise RuntimeError("More than one MP3 found in input/: " + ", ".join(x.name for x in files))
    return files[0]


def find_lyrics(cfg: dict[str, Any]) -> Path:
    candidates: list[Path] = []
    if cfg.get("lyrics_file"):
        candidates.append(ROOT / str(cfg["lyrics_file"]))
    candidates += [INPUT_DIR / "lyrics_acestep.txt", INPUT_DIR / "lyrics.txt"]
    for path in candidates:
        if path.exists():
            return path
    raise RuntimeError("Lyrics not found. Create input/lyrics.txt or input/lyrics_acestep.txt.")


def child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env
