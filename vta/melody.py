from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import LOG_DIR, ROOT, log, run_logged, seed_python


def extract_melody(
    cfg: dict[str, Any],
    vocals: Path,
    track_work: Path,
) -> dict[str, Any]:
    out_dir = track_work / "melody"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "melody.json"

    if cfg.get("reuse_melody", True) and json_path.exists():
        try:
            cached = json.loads(json_path.read_text(encoding="utf-8"))
            if (
                int(cached.get("source_mtime_ns", 0)) >= vocals.stat().st_mtime_ns
                and cached.get("notes")
            ):
                log(f"[Melody] Reusing {len(cached['notes'])} cached notes.")
                return cached
        except Exception:
            pass

    script = ROOT / "scripts" / "extract_melody.py"
    cmd = [
        str(seed_python()),
        str(script),
        "--vocals", str(vocals),
        "--output", str(json_path),
        "--fmin", str(cfg.get("melody_fmin", 55.0)),
        "--fmax", str(cfg.get("melody_fmax", 440.0)),
        "--min-note-ms", str(cfg.get("melody_min_note_ms", 70)),
        "--max-gap-ms", str(cfg.get("melody_max_gap_ms", 90)),
    ]

    log("[Melody] Extracting F0 / note events from original vocals...")
    run_logged(cmd, LOG_DIR / "melody.log", cwd=ROOT, check=True)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["source_mtime_ns"] = vocals.stat().st_mtime_ns
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[Melody] Notes: {len(data.get('notes', []))}")
    return data
