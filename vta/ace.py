from __future__ import annotations

import hashlib
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


def _mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".flac":
        return "audio/flac"
    return "audio/wav"


def _fingerprint(cfg: dict[str, Any], source: Path, reference: Path,
                 lyrics_path: Path) -> str:
    payload = {
        "source": str(source.resolve()),
        "source_size": source.stat().st_size,
        "source_mtime_ns": source.stat().st_mtime_ns,
        "reference": str(reference.resolve()),
        "reference_size": reference.stat().st_size,
        "reference_mtime_ns": reference.stat().st_mtime_ns,
        "lyrics_sha256": hashlib.sha256(lyrics_path.read_bytes()).hexdigest(),
        "language": cfg.get("vocal_language", "uk"),
        "caption": cfg.get("caption", ""),
        "model": cfg.get("ace_model", "acestep-v15-turbo"),
        "cover_strength": cfg.get("cover_strength", 1.0),
        "cover_noise_strength": cfg.get("cover_noise_strength", 0.25),
        "steps": cfg.get("ace_inference_steps", 8),
        "batch": cfg.get("ace_batch_size", 1),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def generate(cfg: dict[str, Any], base_url: str, source: Path, reference: Path,
             lyrics_path: Path, track_work: Path) -> list[Path]:
    guide_dir = track_work / "ace_guides"
    guide_dir.mkdir(parents=True, exist_ok=True)
    manifest = guide_dir / "manifest.json"
    fingerprint = _fingerprint(cfg, source, reference, lyrics_path)

    if cfg.get("reuse_ace_guides", True) and manifest.exists():
        try:
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            cached = sorted(guide_dir.glob("guide_*.wav"))
            if saved.get("fingerprint") == fingerprint and cached and all(
                p.stat().st_size > 0 for p in cached
            ):
                log(f"[ACE] Reusing {len(cached)} matching cached guide(s).")
                return cached
        except Exception:
            pass

    # Old guides without a matching manifest are deliberately not reused.
    for stale in guide_dir.glob("guide_*.wav"):
        try:
            stale.unlink()
        except OSError:
            pass

    language = str(cfg.get("vocal_language", "uk"))
    fields = {
        "prompt": str(cfg.get("caption", "")),
        "lyrics": lyrics_text(lyrics_path, language),
        "vocal_language": language,
        "task_type": "cover",
        "audio_cover_strength": str(cfg.get("cover_strength", 1.0)),
        "cover_noise_strength": str(cfg.get("cover_noise_strength", 0.25)),
        "inference_steps": str(cfg.get("ace_inference_steps", 8)),
        "batch_size": str(cfg.get("ace_batch_size", 1)),
        "audio_format": "wav",
        "model": str(cfg.get("ace_model", "acestep-v15-turbo")),
        "thinking": "false",
        "use_cot_caption": "false",
        "use_cot_language": "false",
        "use_format": "false",
    }
    files = {
        "src_audio": (source.name, source, _mime(source)),
        "reference_audio": (reference.name, reference, _mime(reference)),
    }

    log(
        "[ACE] Submitting faithful cover task "
        f"(cover={fields['audio_cover_strength']}, "
        f"melody={fields['cover_noise_strength']})..."
    )
    payload = post_multipart(f"{base_url}/release_task", fields, files, 240)
    if payload.get("code") != 200:
        raise RuntimeError(f"ACE release_task failed: {payload}")

    task_id = payload["data"]["task_id"]
    log(f"[ACE] Task ID: {task_id}")
    last_log = 0.0
    while True:
        time.sleep(3)
        result = post_json(f"{base_url}/query_result", {"task_id_list": [task_id]}, 60)
        if not result.get("data"):
            continue
        item = result["data"][0]
        status = int(item.get("status", 0))
        if status == 0:
            now = time.time()
            if now - last_log >= 15:
                log("[ACE] Generating...")
                last_log = now
            continue
        if status == 2:
            raise RuntimeError(f"ACE generation failed: {item.get('result')}")
        raw = item.get("result") or "[]"
        results = json.loads(raw) if isinstance(raw, str) else raw
        if not results:
            raise RuntimeError("ACE completed but returned no audio files.")
        break

    guides: list[Path] = []
    for index, item in enumerate(results, 1):
        file_url = item.get("file")
        if not file_url:
            continue
        url = (
            str(file_url)
            if str(file_url).startswith("http")
            else urllib.parse.urljoin(base_url, str(file_url))
        )
        out = guide_dir / f"guide_{index:02d}.wav"
        download(url, out)
        guides.append(out)
        log(f"[ACE] Guide {index}: {out.name}")

    if not guides:
        raise RuntimeError("ACE returned no downloadable audio URLs.")

    manifest.write_text(
        json.dumps({"fingerprint": fingerprint}, indent=2),
        encoding="utf-8",
    )
    return guides
