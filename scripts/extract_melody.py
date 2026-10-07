from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import librosa
import numpy as np


def midi_from_hz(hz: np.ndarray) -> np.ndarray:
    out = np.full_like(hz, np.nan, dtype=float)
    mask = np.isfinite(hz) & (hz > 0)
    out[mask] = 69.0 + 12.0 * np.log2(hz[mask] / 440.0)
    return out


def median_smooth(values: np.ndarray, radius: int = 2) -> np.ndarray:
    out = values.copy()
    for i in range(len(values)):
        if not np.isfinite(values[i]):
            continue
        lo = max(0, i - radius)
        hi = min(len(values), i + radius + 1)
        chunk = values[lo:hi]
        chunk = chunk[np.isfinite(chunk)]
        if len(chunk):
            out[i] = float(np.median(chunk))
    return out


def bridge_short_gaps(midi: np.ndarray, max_frames: int) -> np.ndarray:
    out = midi.copy()
    i = 0
    n = len(out)
    while i < n:
        if np.isfinite(out[i]):
            i += 1
            continue
        start = i
        while i < n and not np.isfinite(out[i]):
            i += 1
        end = i
        gap = end - start
        if (
            gap <= max_frames
            and start > 0
            and end < n
            and np.isfinite(out[start - 1])
            and np.isfinite(out[end])
            and abs(out[start - 1] - out[end]) <= 1.2
        ):
            a = out[start - 1]
            b = out[end]
            for j in range(gap):
                out[start + j] = a + (b - a) * ((j + 1) / (gap + 1))
    return out


def segment_notes(
    times: np.ndarray,
    midi: np.ndarray,
    voiced_prob: np.ndarray | None,
    min_note_s: float,
) -> list[dict]:
    notes: list[dict] = []
    active_start = None
    active_values: list[float] = []
    active_probs: list[float] = []
    prev_q = None

    def close(end_idx: int) -> None:
        nonlocal active_start, active_values, active_probs, prev_q
        if active_start is None or not active_values:
            active_start = None
            active_values = []
            active_probs = []
            prev_q = None
            return
        start_t = float(times[active_start])
        end_t = float(times[min(end_idx, len(times) - 1)])
        if end_t <= start_t:
            end_t = start_t + 0.05
        if end_t - start_t >= min_note_s:
            med = float(np.median(active_values))
            notes.append({
                "start": start_t,
                "end": end_t,
                "midi": int(np.clip(round(med), 24, 96)),
                "midi_float": med,
                "confidence": float(np.mean(active_probs)) if active_probs else 1.0,
            })
        active_start = None
        active_values = []
        active_probs = []
        prev_q = None

    for i, value in enumerate(midi):
        if not np.isfinite(value):
            close(i)
            continue

        q = int(round(float(value)))
        if active_start is None:
            active_start = i
            prev_q = q
            active_values = [float(value)]
            if voiced_prob is not None and i < len(voiced_prob) and np.isfinite(voiced_prob[i]):
                active_probs = [float(voiced_prob[i])]
            continue

        # A persistent semitone transition should start a new note.
        if prev_q is not None and abs(q - prev_q) >= 1:
            look = midi[i:min(len(midi), i + 4)]
            finite = look[np.isfinite(look)]
            if len(finite) >= 2 and abs(round(float(np.median(finite))) - prev_q) >= 1:
                close(i)
                active_start = i
                prev_q = q
                active_values = [float(value)]
                if voiced_prob is not None and i < len(voiced_prob) and np.isfinite(voiced_prob[i]):
                    active_probs = [float(voiced_prob[i])]
                continue

        active_values.append(float(value))
        if voiced_prob is not None and i < len(voiced_prob) and np.isfinite(voiced_prob[i]):
            active_probs.append(float(voiced_prob[i]))
        prev_q = int(round(float(np.median(active_values))))

    close(len(midi) - 1)
    return notes


def merge_tiny_notes(notes: list[dict], min_note_s: float) -> list[dict]:
    if not notes:
        return notes
    out: list[dict] = []
    for note in notes:
        dur = note["end"] - note["start"]
        if dur >= min_note_s or not out:
            out.append(note)
            continue
        prev = out[-1]
        if note["start"] - prev["end"] <= 0.08 and abs(note["midi"] - prev["midi"]) <= 2:
            total = max(1e-6, (prev["end"] - prev["start"]) + dur)
            prev["midi_float"] = (
                prev["midi_float"] * (prev["end"] - prev["start"])
                + note["midi_float"] * dur
            ) / total
            prev["midi"] = int(round(prev["midi_float"]))
            prev["end"] = note["end"]
            prev["confidence"] = (prev["confidence"] + note["confidence"]) / 2
        else:
            out.append(note)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocals", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--fmin", type=float, default=55.0)
    ap.add_argument("--fmax", type=float, default=440.0)
    ap.add_argument("--min-note-ms", type=float, default=70.0)
    ap.add_argument("--max-gap-ms", type=float, default=90.0)
    args = ap.parse_args()

    vocals = Path(args.vocals)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    sr = 22050
    hop = 256
    y, _ = librosa.load(vocals, sr=sr, mono=True)

    f0, voiced_flag, voiced_prob = librosa.pyin(
        y,
        fmin=float(args.fmin),
        fmax=float(args.fmax),
        sr=sr,
        frame_length=2048,
        hop_length=hop,
    )
    times = librosa.times_like(f0, sr=sr, hop_length=hop)
    midi = midi_from_hz(f0)
    midi = median_smooth(midi, radius=2)

    gap_frames = max(1, int(round((args.max_gap_ms / 1000.0) * sr / hop)))
    midi = bridge_short_gaps(midi, gap_frames)

    notes = segment_notes(
        times=times,
        midi=midi,
        voiced_prob=voiced_prob,
        min_note_s=max(0.03, args.min_note_ms / 1000.0),
    )
    notes = merge_tiny_notes(notes, max(0.03, args.min_note_ms / 1000.0))

    payload = {
        "sample_rate": sr,
        "hop_length": hop,
        "duration": float(len(y) / sr),
        "source_mtime_ns": vocals.stat().st_mtime_ns,
        "notes": notes,
    }
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[Melody] extracted notes={len(notes)} duration={payload['duration']:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
