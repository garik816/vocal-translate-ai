from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .common import log
from .net import download, post_json, post_multipart


def lyrics_text(path: Path, language: str) -> str:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        raise RuntimeError(f"Lyrics file is empty: {path}")
    if "# Lyric" in text:
        return text
    return f"# Languages\n{language}\n\n# Lyric\n{text}\n"


def generate(cfg: dict[str, Any], base_url: str, source: Path, reference: Path,
             lyrics_path: Path, track_work: Path) -> list[Path]:
    language = str(cfg.get("vocal_language", "uk"))
    fields = {
        "prompt": str(cfg.get("caption", "isolated lead vocal, preserve melody and timing")),
        "lyrics": lyrics_text(lyrics_path, language),
        "vocal_language": language,
        "task_type": "cover",
        "audio_cover_strength": str(cfg.get("cover_strength", 0.88)),
        "inference_steps": str(cfg.get("ace_inference_steps", 8)),
        "batch_size": str(cfg.get("ace_batch_size", 1)),
        "audio_format": "wav",
        "model": str(cfg.get("ace_model", "acestep-v15-turbo")),
        "thinking": "false",
        "use_cot_caption": "false",
        "use_cot_language": "false",
        "use_format": "false",
    }
    files = {"src_audio": (source.name, source, "audio/wav"),
             "reference_audio": (reference.name, reference, "audio/wav")}
    log("[ACE] Submitting translated cover task...")
    payload = post_multipart(f"{base_url}/release_task", fields, files, 240)
    if payload.get("code") != 200:
        raise RuntimeError(f"ACE release_task failed: {payload}")
    task_id = payload["data"]["task_id"]
    log(f"[ACE] Task ID: {task_id}")
    while True:
        time.sleep(3)
        result = post_json(f"{base_url}/query_result", {"task_id_list": [task_id]}, 60)
        if not result.get("data"):
            continue
        item = result["data"][0]
        status = int(item.get("status", 0))
        if status == 0:
            log("[ACE] Generating...")
            continue
        if status == 2:
            raise RuntimeError(f"ACE generation failed: {item.get('result')}")
        raw = item.get("result") or "[]"
        results = json.loads(raw) if isinstance(raw, str) else raw
        if not results:
            raise RuntimeError("ACE completed but returned no audio files.")
        break
    guide_dir = track_work / "ace_guides"
    guide_dir.mkdir(parents=True, exist_ok=True)
    guides: list[Path] = []
    for index, item in enumerate(results, 1):
        file_url = item.get("file")
        if not file_url:
            continue
        url = str(file_url) if str(file_url).startswith("http") else urllib.parse.urljoin(base_url, str(file_url))
        out = guide_dir / f"guide_{index:02d}.wav"
        download(url, out)
        guides.append(out)
        log(f"[ACE] Guide {index}: {out.name}")
    if not guides:
        raise RuntimeError("ACE returned no downloadable audio URLs.")
    return guides
