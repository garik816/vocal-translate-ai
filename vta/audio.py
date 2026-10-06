from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .common import INPUT_DIR, LOG_DIR, ROOT, ace_python, log, run_logged


def locate_stems(root: Path) -> dict[str, Path]:
    for vocals in root.rglob("vocals.wav"):
        parent = vocals.parent
        stems = {"vocals": vocals, "bass": parent / "bass.wav",
                 "drums": parent / "drums.wav", "other": parent / "other.wav"}
        if all(x.exists() for x in stems.values()):
            return stems
    raise RuntimeError(f"Demucs stems not found under {root}")


def run_demucs(original: Path, track_work: Path, cfg: dict[str, Any]) -> dict[str, Path]:
    separated = track_work / "separated"
    if cfg.get("reuse_demucs", True) and separated.exists():
        try:
            stems = locate_stems(separated)
            log(f"[Demucs] Reusing stems: {stems['vocals'].parent}")
            return stems
        except Exception:
            pass
    if separated.exists():
        shutil.rmtree(separated)
    separated.mkdir(parents=True, exist_ok=True)
    model = str(cfg.get("demucs_model", "htdemucs"))
    device = str(cfg.get("demucs_device", "cuda"))
    base = [str(ace_python()), "-m", "demucs", "-n", model, "-o", str(separated)]
    rc = run_logged(base + ["--device", device, str(original)], LOG_DIR / "demucs.log",
                    cwd=ROOT, check=False)
    if rc != 0 and device.lower() != "cpu" and cfg.get("demucs_cpu_fallback", True):
        log("[Demucs] CUDA failed; retrying on CPU.")
        run_logged(base + ["--device", "cpu", str(original)], LOG_DIR / "demucs.log",
                   cwd=ROOT, check=True)
    elif rc != 0:
        raise RuntimeError("Demucs failed. See logs/demucs.log")
    stems = locate_stems(separated)
    log(f"[Demucs] Stems ready: {stems['vocals'].parent}")
    return stems


def build_instrumental(stems: dict[str, Path], track_work: Path, ffmpeg: str) -> Path:
    output = track_work / "instrumental.wav"
    filt = "[0:a][1:a][2:a]amix=inputs=3:duration=longest:normalize=0,alimiter=limit=0.98[out]"
    run_logged([ffmpeg, "-y", "-i", str(stems["bass"]), "-i", str(stems["drums"]),
                "-i", str(stems["other"]), "-filter_complex", filt, "-map", "[out]",
                "-c:a", "pcm_s24le", str(output)], LOG_DIR / "ffmpeg.log")
    return output


def build_reference(vocals: Path, track_work: Path, ffmpeg: str,
                    cfg: dict[str, Any]) -> Path:
    manual = INPUT_DIR / "reference.wav"
    if manual.exists():
        log("[Reference] Using input/reference.wav")
        return manual
    output = track_work / "reference.wav"
    seconds = int(cfg.get("reference_seconds", 22))
    threshold = str(cfg.get("reference_silence_threshold", "-45dB"))
    af = f"silenceremove=start_periods=1:start_silence=0.20:start_threshold={threshold}"
    run_logged([ffmpeg, "-y", "-i", str(vocals), "-af", af, "-t", str(seconds),
                "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(output)],
               LOG_DIR / "ffmpeg.log")
    log(f"[Reference] Auto reference created: {output.name}")
    return output


def render_final(cfg: dict[str, Any], original: Path, instrumental: Path,
                 vocals: list[Path], track_work: Path, ffmpeg: str, out_dir: Path) -> list[Path]:
    vg = float(cfg.get("mix_vocal_gain_db", 0.0))
    ig = float(cfg.get("mix_instrumental_gain_db", 0.0))
    bitrate = str(cfg.get("output_bitrate", "320k"))
    results: list[Path] = []
    for index, vocal in enumerate(vocals, 1):
        mixed = track_work / f"mixed_{index:02d}.wav"
        filt = (f"[0:a]volume={ig}dB[inst];[1:a]volume={vg}dB[voc];"
                "[inst][voc]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.98[out]")
        run_logged([ffmpeg, "-y", "-i", str(instrumental), "-i", str(vocal),
                    "-filter_complex", filt, "-map", "[out]", "-c:a", "pcm_s24le",
                    str(mixed)], LOG_DIR / "ffmpeg.log")
        suffix = "" if len(vocals) == 1 else f"_{index:02d}"
        final = out_dir / f"{original.stem}_uk{suffix}.mp3"
        run_logged([ffmpeg, "-y", "-i", str(mixed), "-i", str(original),
                    "-map", "0:a:0", "-map", "1:v?", "-map_metadata", "1",
                    "-c:a", "libmp3lame", "-b:a", bitrate, "-c:v", "copy",
                    "-id3v2_version", "3", str(final)], LOG_DIR / "ffmpeg.log")
        results.append(final)
        log(f"[Output] {final}")
    return results
