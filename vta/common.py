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
SEED_DIR = ROOT / "runtime" / "seed-vc"
OPENUTAU_DIR = ROOT / "runtime" / "OpenUtau-lunai"
DIFFSINGER_DIR = ROOT / "runtime" / "diffsinger"
# Kept only so old v6 helper modules do not break when imported manually.
ACE_DIR = ROOT / "runtime" / "ACE-Step-1.5"
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


def seed_python() -> Path:
    path = SEED_DIR / ".venv" / "Scripts" / "python.exe"
    require(path, "Seed-VC Python")
    return path


def ace_python() -> Path:
    path = ACE_DIR / ".venv" / "Scripts" / "python.exe"
    require(path, "Legacy ACE-Step Python")
    return path


def find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH. Run SETUP_ONLY.bat.")
    return ffmpeg


def find_input_mp3s() -> list[Path]:
    files = sorted(INPUT_DIR.glob("*.mp3"), key=lambda p: p.name.casefold())
    if not files:
        raise RuntimeError("No MP3 found in input/. Put one or more original MP3 files there.")
    return files


def find_lyrics_for(original: Path, cfg: dict[str, Any], total_tracks: int) -> Path:
    stem = original.stem
    candidates = [
        INPUT_DIR / "lyrics" / f"{stem}.txt",
        INPUT_DIR / f"{stem}.txt",
    ]

    if total_tracks == 1:
        if cfg.get("lyrics_file"):
            candidates.append(ROOT / str(cfg["lyrics_file"]))
        candidates += [INPUT_DIR / "lyrics.txt"]

    if total_tracks > 1 and cfg.get("batch_shared_lyrics", False):
        if cfg.get("lyrics_file"):
            candidates.append(ROOT / str(cfg["lyrics_file"]))
        candidates += [INPUT_DIR / "lyrics.txt"]

    for path in candidates:
        if path.exists():
            return path

    expected = [
        str((INPUT_DIR / "lyrics" / f"{stem}.txt").relative_to(ROOT)),
        str((INPUT_DIR / f"{stem}.txt").relative_to(ROOT)),
    ]
    raise RuntimeError(
        f"No target lyrics found for '{original.name}'. Create " + " or ".join(expected)
    )


def child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env
