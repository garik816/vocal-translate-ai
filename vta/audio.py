from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .common import INPUT_DIR, LOG_DIR, ROOT, ace_python, log, run_logged


def locate_stems(root: Path) -> dict[str, Path]:
    for vocals in root.rglob("vocals.wav"):
        parent = vocals.parent
        stems = {
            "vocals": vocals,
            "bass": parent / "bass.wav",
            "drums": parent / "drums.wav",
            "other": parent / "other.wav",
        }
        if all(x.exists() for x in stems.values()):
            return stems
    raise RuntimeError(f"Demucs stems not found under {root}")


def _run_demucs_command(cmd: list[str], input_audio: Path,
                        cfg: dict[str, Any]) -> None:
    device = str(cfg.get("demucs_device", "cuda"))
    rc = run_logged(
        cmd + ["--device", device, str(input_audio)],
        LOG_DIR / "demucs.log",
        cwd=ROOT,
        check=False,
    )
    if rc != 0 and device.lower() != "cpu" and cfg.get("demucs_cpu_fallback", True):
        log("[Demucs] CUDA failed; retrying on CPU.")
        run_logged(
            cmd + ["--device", "cpu", str(input_audio)],
            LOG_DIR / "demucs.log",
            cwd=ROOT,
            check=True,
        )
    elif rc != 0:
        raise RuntimeError("Demucs failed. See logs/demucs.log")


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
    cmd = [
        str(ace_python()), "-m", "demucs",
        "-n", model,
        "-o", str(separated),
    ]
    _run_demucs_command(cmd, original, cfg)

    stems = locate_stems(separated)
    log(f"[Demucs] Stems ready: {stems['vocals'].parent}")
    return stems


def isolate_guide_vocals(guides: list[Path], track_work: Path,
                         cfg: dict[str, Any]) -> list[Path]:
    if not cfg.get("post_ace_demucs", True):
        return guides

    model = str(cfg.get("demucs_model", "htdemucs"))
    root = track_work / "ace_vocals"
    root.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []

    for index, guide in enumerate(guides, 1):
        final = root / f"guide_vocal_{index:02d}.wav"
        if (
            cfg.get("reuse_post_ace_demucs", True)
            and final.exists()
            and final.stat().st_size > 0
            and final.stat().st_mtime_ns >= guide.stat().st_mtime_ns
        ):
            log(f"[Demucs] Reusing isolated ACE vocal: {final.name}")
            outputs.append(final)
            continue

        work = root / f"candidate_{index:02d}"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)

        log(f"[Demucs] Isolating vocals from ACE guide {index}/{len(guides)}...")
        cmd = [
            str(ace_python()), "-m", "demucs",
            "-n", model,
            "--two-stems", "vocals",
            "-o", str(work),
        ]
        _run_demucs_command(cmd, guide, cfg)

        candidates = sorted(work.rglob("vocals.wav"))
        if not candidates:
            raise RuntimeError(
                f"Demucs produced no vocals.wav for ACE guide {index}."
            )
        shutil.copy2(candidates[0], final)
        outputs.append(final)
        log(f"[Demucs] Clean ACE vocal: {final.name}")

    return outputs


def build_instrumental(stems: dict[str, Path], track_work: Path, ffmpeg: str) -> Path:
    output = track_work / "instrumental.wav"
    # level=false is important: FFmpeg's limiter must not auto-raise the
    # instrumental level. We only want peak protection.
    filt = (
        "[0:a][1:a][2:a]"
        "amix=inputs=3:duration=longest:normalize=0,"
        "alimiter=limit=0.98:level=false[out]"
    )
    run_logged(
        [
            ffmpeg, "-y",
            "-i", str(stems["bass"]),
            "-i", str(stems["drums"]),
            "-i", str(stems["other"]),
            "-filter_complex", filt,
            "-map", "[out]",
            "-c:a", "pcm_s24le",
            str(output),
        ],
        LOG_DIR / "ffmpeg.log",
    )
    return output


def build_cover_source(stems: dict[str, Path], instrumental: Path,
                       track_work: Path, ffmpeg: str,
                       cfg: dict[str, Any]) -> Path:
    mode = str(cfg.get("ace_source_mode", "full_mix")).lower()
    if mode != "delexicalized_mix":
        return instrumental

    output = track_work / "ace_cover_source.wav"
    cutoff = int(cfg.get("cover_source_lowpass_hz", 450))

    # Remove most consonant/formant detail from the original vocal while
    # retaining its F0/rhythm as a neutral melody carrier. This reduces
    # source-language leakage into the translated pronunciation.
    filt = (
        f"[1:a]highpass=f=70,lowpass=f={cutoff},volume=-3dB[mel];"
        "[0:a][mel]amix=inputs=2:duration=longest:normalize=0,"
        "alimiter=limit=0.98:level=false[out]"
    )
    run_logged(
        [
            ffmpeg, "-y",
            "-i", str(instrumental),
            "-i", str(stems["vocals"]),
            "-filter_complex", filt,
            "-map", "[out]",
            "-c:a", "pcm_s24le",
            str(output),
        ],
        LOG_DIR / "ffmpeg.log",
    )
    log(
        f"[ACE] Built delexicalized cover source "
        f"(vocal low-pass {cutoff} Hz)."
    )
    return output


def build_reference(vocals: Path, track_work: Path, ffmpeg: str,
                    cfg: dict[str, Any], original: Path) -> Path:
    per_track = INPUT_DIR / "reference" / f"{original.stem}.wav"
    sibling = INPUT_DIR / f"{original.stem}.wav"
    shared = INPUT_DIR / "reference.wav"

    for manual in (per_track, sibling, shared):
        if manual.exists():
            log(f"[Reference] Using {manual.relative_to(ROOT)}")
            return manual

    output = track_work / "reference.wav"
    seconds = int(cfg.get("reference_seconds", 22))
    threshold = str(cfg.get("reference_silence_threshold", "-45dB"))
    af = (
        f"silenceremove=start_periods=1:"
        f"start_silence=0.20:start_threshold={threshold}"
    )
    run_logged(
        [
            ffmpeg, "-y",
            "-i", str(vocals),
            "-af", af,
            "-t", str(seconds),
            "-ac", "1",
            "-ar", "44100",
            "-c:a", "pcm_s16le",
            str(output),
        ],
        LOG_DIR / "ffmpeg.log",
    )
    log(f"[Reference] Auto reference created: {output.name}")
    return output


def render_guide_preview(cfg: dict[str, Any], original: Path,
                         instrumental: Path, vocal: Path,
                         track_work: Path, ffmpeg: str,
                         out_dir: Path) -> Path:
    vg = float(cfg.get("mix_vocal_gain_db", 0.0))
    ig = float(cfg.get("mix_instrumental_gain_db", 0.0))
    bitrate = str(cfg.get("output_bitrate", "320k"))
    lang = str(cfg.get("vocal_language", "uk"))
    final = out_dir / f"{original.stem}_{lang}_guide.mp3"
    filt = (
        f"[0:a]volume={ig}dB[inst];"
        f"[1:a]volume={vg}dB[voc];"
        "[inst][voc]amix=inputs=2:duration=longest:normalize=0,"
        "alimiter=limit=0.98:level=false[out]"
    )
    run_logged(
        [
            ffmpeg, "-y",
            "-i", str(instrumental),
            "-i", str(vocal),
            "-filter_complex", filt,
            "-map", "[out]",
            "-c:a", "libmp3lame",
            "-b:a", bitrate,
            str(final),
        ],
        LOG_DIR / "ffmpeg.log",
    )
    log(f"[Output] Guide preview (before Seed-VC): {final}")
    return final


def render_final(cfg: dict[str, Any], original: Path, instrumental: Path,
                 vocals: list[Path], track_work: Path, ffmpeg: str,
                 out_dir: Path) -> list[Path]:
    vg = float(cfg.get("mix_vocal_gain_db", 0.0))
    ig = float(cfg.get("mix_instrumental_gain_db", 0.0))
    bitrate = str(cfg.get("output_bitrate", "320k"))
    lang = str(cfg.get("vocal_language", "uk"))
    results: list[Path] = []

    for index, vocal in enumerate(vocals, 1):
        mixed = track_work / f"mixed_{index:02d}.wav"
        filt = (
            f"[0:a]volume={ig}dB[inst];"
            f"[1:a]volume={vg}dB[voc];"
            "[inst][voc]"
            "amix=inputs=2:duration=longest:normalize=0,"
            "alimiter=limit=0.98:level=false[out]"
        )
        run_logged(
            [
                ffmpeg, "-y",
                "-i", str(instrumental),
                "-i", str(vocal),
                "-filter_complex", filt,
                "-map", "[out]",
                "-c:a", "pcm_s24le",
                str(mixed),
            ],
            LOG_DIR / "ffmpeg.log",
        )

        suffix = "" if len(vocals) == 1 else f"_{index:02d}"
        final = out_dir / f"{original.stem}_{lang}{suffix}.mp3"
        run_logged(
            [
                ffmpeg, "-y",
                "-i", str(mixed),
                "-i", str(original),
                "-map", "0:a:0",
                "-map", "1:v?",
                "-map_metadata", "1",
                "-c:a", "libmp3lame",
                "-b:a", bitrate,
                "-c:v", "copy",
                "-id3v2_version", "3",
                str(final),
            ],
            LOG_DIR / "ffmpeg.log",
        )
        results.append(final)
        log(f"[Output] {final}")

    return results
