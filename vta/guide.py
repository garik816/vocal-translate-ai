"""Import a full-timeline Ukrainian vocal and align its overall duration.

A guide is *already sung*: ACE-Step Cover output (after vocal separation) or
a human/other-model recording. This path intentionally bypasses Whisper notes,
heuristic syllables, USTX, and DiffSinger.

Global duration correction does not solve within-phrase drift; that requires
audible review and/or a time-warp editor.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from .common import INPUT_DIR, LOG_DIR, log, run_logged

SUPPORTED_EXTENSIONS = (".wav", ".flac", ".mp3", ".m4a")


def find_external_guide(original: Path, total_tracks: int) -> Path:
    """Locate a user-supplied sung vocal or a previously generated ACE mix."""
    candidates: list[Path] = []
    for extension in SUPPORTED_EXTENSIONS:
        candidates.append(INPUT_DIR / "guide" / f"{original.stem}{extension}")
        candidates.append(INPUT_DIR / f"{original.stem}_guide{extension}")
    if total_tracks == 1:
        for extension in SUPPORTED_EXTENSIONS:
            candidates.append(INPUT_DIR / f"guide{extension}")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "No sung Ukrainian guide found for " + repr(original.name) + ". "
        "Put the vocal at input/guide/<song-name>.wav (same name as MP3), "
        "or input/guide.wav for a single song. "
        "To process a full music mix, set external_guide_is_mix=true."
    )


def audio_duration(path: Path) -> float:
    """Read duration in seconds using the FFmpeg companion executable."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is missing. Install FFmpeg; see SETUP_ONLY.bat.")
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True,
    )
    seconds = float(proc.stdout.strip())
    if not (0.0 < seconds < 14400.0):
        raise ValueError(f"Invalid audio duration for {path}: {seconds}")
    return seconds


def timing_ratio(
    source_seconds: float,
    target_seconds: float,
    max_deviation: float = 0.08,
) -> float:
    """atempo factor needed to fit a full-timeline guide to a source recording.

    Reject grossly different durations rather than silently changing song form.
    """
    if source_seconds <= 0 or target_seconds <= 0:
        raise ValueError("Guide and original must have positive durations.")
    if not 0 <= max_deviation <= 0.5:
        raise ValueError("guide_max_duration_deviation must be within [0, 0.5].")
    ratio = source_seconds / target_seconds
    if abs(ratio - 1.0) > max_deviation:
        raise ValueError(
            "Guide duration differs too much from original: "
            f"{source_seconds:.2f}s vs {target_seconds:.2f}s. "
            "Generate a cover with the same duration, or manually align the "
            "sung guide. The pipeline does not rearrange musical sections."
        )
    return ratio


def prepare_guide(
    cfg: dict[str, Any],
    original: Path,
    vocal: Path,
    track_work: Path,
    ffmpeg: str,
) -> Path:
    """Conform imported vocals to original timeline and mono PCM for Seed-VC."""
    source_s = audio_duration(vocal)
    target_s = audio_duration(original)
    ratio = timing_ratio(
        source_s,
        target_s,
        float(cfg.get("guide_max_duration_deviation", 0.08)),
    )
    offset_s = float(cfg.get("guide_offset_seconds", 0.0))
    if abs(offset_s) > 60.0:
        raise ValueError("guide_offset_seconds must be between -60 and 60.")

    output = track_work / "imported_guide" / "vocal_aligned.wav"
    output.parent.mkdir(parents=True, exist_ok=True)

    filters = []
    # Normal pitch-preserving tempo correction. Within-note drift still needs
    # human listening; this only matches the beginning/end of the full track.
    if abs(ratio - 1.0) > 0.0005:
        filters.append(f"atempo={ratio:.8f}")
    if offset_s > 0.0:
        filters.append(f"adelay=delays={int(round(offset_s * 1000))}:all=1")
    elif offset_s < 0.0:
        filters.append(f"atrim=start={-offset_s:.6f}")
        filters.append("asetpts=PTS-STARTPTS")
    # No generated voice is allowed to lengthen the original song.
    filters.extend([
        "asetpts=PTS-STARTPTS",
        f"apad=whole_dur={target_s:.6f}",
        f"atrim=duration={target_s:.6f}",
    ])
    cmd = [
        ffmpeg, "-y", "-i", str(vocal), "-vn",
        "-af", ",".join(filters),
        "-ac", "1", "-ar", "44100", "-c:a", "pcm_s24le",
        str(output),
    ]
    log(
        f"[Guide] Vocal={source_s:.2f}s, original={target_s:.2f}s, "
        f"atempo={ratio:.5f}, offset={offset_s:+.3f}s"
    )
    run_logged(cmd, LOG_DIR / "ffmpeg.log")
    if not output.is_file() or output.stat().st_size <= 4096:
        raise RuntimeError("FFmpeg did not create aligned guide WAV.")
    return output
