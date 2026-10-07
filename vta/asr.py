from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import LOG_DIR, ROOT, log, run_logged, seed_python


def extract_source_lyrics(
    cfg: dict[str, Any],
    original: Path,
    vocals: Path,
    track_work: Path,
) -> dict[str, Any] | None:
    if not cfg.get("extract_source_lyrics", True):
        return None

    out_dir = track_work / "source_lyrics"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "lyrics_source.json"

    if cfg.get("reuse_source_lyrics", True) and json_path.exists():
        try:
            cached = json.loads(json_path.read_text(encoding="utf-8"))
            source_mtime = max(original.stat().st_mtime_ns, vocals.stat().st_mtime_ns)
            if (
                int(cached.get("source_mtime_ns", 0)) >= source_mtime
                and cached.get("segments") is not None
            ):
                log("[ASR] Reusing cached source transcript.")
                return cached
        except Exception:
            pass

    model = str(cfg.get("asr_model", "turbo"))
    device = str(cfg.get("asr_device", "auto"))
    language = str(cfg.get("asr_language", "auto"))
    model_dir = ROOT / "runtime" / "models" / "whisper"
    script = ROOT / "scripts" / "extract_source_lyrics.py"

    cmd = [
        str(seed_python()),
        str(script),
        "--mp3", str(original),
        "--vocals", str(vocals),
        "--out-dir", str(out_dir),
        "--model", model,
        "--device", device,
        "--language", language,
        "--model-dir", str(model_dir),
    ]

    log(f"[ASR] Extracting source lyrics with Whisper model={model}, device={device}...")
    run_logged(cmd, LOG_DIR / "asr.log", cwd=ROOT, check=True)

    if not json_path.exists():
        raise RuntimeError("ASR finished but lyrics_source.json was not created.")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["source_mtime_ns"] = max(original.stat().st_mtime_ns, vocals.stat().st_mtime_ns)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"[ASR] Source lyrics: {out_dir / 'lyrics_source.txt'}")
    log(f"[ASR] Timings: {out_dir / 'lyrics_source.srt'}")
    return data
