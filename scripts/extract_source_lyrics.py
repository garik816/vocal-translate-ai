from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path


def clean_text(text: str) -> str:
    text = text.replace("\ufeff", "").replace("\u200b", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_id3_lyrics(mp3: Path) -> dict:
    result = {"uslt": [], "sylt": []}
    try:
        from mutagen.id3 import ID3
        tags = ID3(mp3)
    except Exception:
        return result

    try:
        for frame in tags.getall("USLT"):
            text = clean_text(getattr(frame, "text", "") or "")
            if text:
                result["uslt"].append({
                    "lang": getattr(frame, "lang", ""),
                    "desc": getattr(frame, "desc", ""),
                    "text": text,
                })
    except Exception:
        pass

    try:
        for frame in tags.getall("SYLT"):
            items = []
            for item in getattr(frame, "text", []) or []:
                if isinstance(item, (tuple, list)) and len(item) >= 2:
                    lyric, stamp = item[0], item[1]
                    items.append({
                        "text": str(lyric),
                        "time_ms": int(stamp),
                    })
            if items:
                result["sylt"].append({
                    "lang": getattr(frame, "lang", ""),
                    "desc": getattr(frame, "desc", ""),
                    "items": items,
                })
    except Exception:
        pass

    return result


def sec_to_srt(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments: list[dict], path: Path) -> None:
    rows = []
    idx = 1
    for seg in segments:
        text = clean_text(str(seg.get("text", "")))
        if not text:
            continue
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start + 0.5))
        if end <= start:
            end = start + 0.5
        rows += [
            str(idx),
            f"{sec_to_srt(start)} --> {sec_to_srt(end)}",
            text,
            "",
        ]
        idx += 1
    path.write_text("\n".join(rows), encoding="utf-8")


def choose_device(requested: str) -> str:
    requested = requested.lower().strip()
    if requested in {"cpu", "cuda"}:
        return requested
    try:
        import torch
        if not torch.cuda.is_available():
            return "cpu"
        total_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
        return "cuda" if total_gb >= 6.0 else "cpu"
    except Exception:
        return "cpu"


def transcribe(vocals: Path, model_name: str, device: str, language: str, model_dir: Path) -> dict:
    import whisper

    model_dir.mkdir(parents=True, exist_ok=True)
    model = whisper.load_model(
        model_name,
        device=device,
        download_root=str(model_dir),
    )

    kwargs = {
        "task": "transcribe",
        "word_timestamps": True,
        "verbose": False,
        "condition_on_previous_text": True,
        "temperature": 0,
        "fp16": device == "cuda",
    }
    if language and language.lower() != "auto":
        kwargs["language"] = language

    result = model.transcribe(str(vocals), **kwargs)

    segments = []
    words = []
    for seg in result.get("segments", []) or []:
        item = {
            "id": int(seg.get("id", len(segments))),
            "start": float(seg.get("start", 0.0)),
            "end": float(seg.get("end", 0.0)),
            "text": clean_text(str(seg.get("text", ""))),
        }
        seg_words = []
        for word in seg.get("words", []) or []:
            w = {
                "start": float(word.get("start", item["start"])),
                "end": float(word.get("end", item["end"])),
                "word": clean_text(str(word.get("word", ""))),
                "probability": float(word.get("probability", 0.0)),
            }
            seg_words.append(w)
            words.append(w)
        item["words"] = seg_words
        segments.append(item)

    return {
        "language": result.get("language"),
        "text": clean_text(result.get("text", "")),
        "segments": segments,
        "words": words,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mp3", required=True)
    ap.add_argument("--vocals", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--model", default="turbo")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--language", default="auto")
    ap.add_argument("--model-dir", required=True)
    args = ap.parse_args()

    mp3 = Path(args.mp3)
    vocals = Path(args.vocals)
    out_dir = Path(args.out_dir)
    model_dir = Path(args.model_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    id3 = read_id3_lyrics(mp3)
    device = choose_device(args.device)
    print(f"[ASR] device={device} model={args.model}", flush=True)

    asr = transcribe(
        vocals=vocals,
        model_name=args.model,
        device=device,
        language=args.language,
        model_dir=model_dir,
    )

    id3_text = ""
    if id3["uslt"]:
        id3_text = id3["uslt"][0]["text"]

    authoritative = id3_text if id3_text else asr["text"]
    source_kind = "id3+whisper-timing" if id3_text else "whisper"

    payload = {
        "source": source_kind,
        "device": device,
        "model": args.model,
        "language": asr.get("language"),
        "text": authoritative,
        "asr_text": asr["text"],
        "id3": id3,
        "segments": asr["segments"],
        "words": asr["words"],
        "source_mtime_ns": max(mp3.stat().st_mtime_ns, vocals.stat().st_mtime_ns),
    }

    (out_dir / "lyrics_source.txt").write_text(
        authoritative.rstrip() + "\n", encoding="utf-8"
    )
    (out_dir / "lyrics_source_asr.txt").write_text(
        asr["text"].rstrip() + "\n", encoding="utf-8"
    )
    write_srt(asr["segments"], out_dir / "lyrics_source.srt")
    (out_dir / "lyrics_source.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[ASR] language={payload['language']} source={source_kind}", flush=True)
    print(f"[ASR] segments={len(asr['segments'])} words={len(asr['words'])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
