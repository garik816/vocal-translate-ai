from __future__ import annotations

import argparse
import shutil
import sys
import traceback
from pathlib import Path

from vta import __version__, asr, audio, diffsinger, melody, seed
from vta.common import (
    LOG_DIR,
    OUT_DIR,
    RUN_LOG,
    SEED_DIR,
    WORK_DIR,
    find_ffmpeg,
    find_input_mp3s,
    find_lyrics_for,
    load_config,
    log,
    require,
)
from vta.gpu import apply_gpu_profile


def export_source_lyrics(original: Path, track_work: Path) -> None:
    src = track_work / "source_lyrics"
    if not src.exists():
        return
    mapping = {
        "lyrics_source.txt": OUT_DIR / f"{original.stem}_source_lyrics.txt",
        "lyrics_source.srt": OUT_DIR / f"{original.stem}_source_lyrics.srt",
        "lyrics_source.json": OUT_DIR / f"{original.stem}_source_lyrics.json",
    }
    for name, dst in mapping.items():
        path = src / name
        if path.exists():
            shutil.copy2(path, dst)


def process_track(
    original: Path,
    cfg: dict,
    ffmpeg: str,
    total_tracks: int,
    extract_only: bool,
) -> list[Path]:
    track_work = WORK_DIR / original.stem
    track_work.mkdir(parents=True, exist_ok=True)

    log("")
    log("=" * 72)
    log(f"TRACK: {original.name}")
    log("=" * 72)

    stems = audio.run_demucs(original, track_work, cfg)
    source_asr = asr.extract_source_lyrics(
        cfg=cfg,
        original=original,
        vocals=stems["vocals"],
        track_work=track_work,
    )
    if cfg.get("export_source_lyrics", True):
        export_source_lyrics(original, track_work)

    if extract_only:
        log("[Mode] Source-lyrics extraction complete; synthesis skipped.")
        return []

    lyrics = find_lyrics_for(original, cfg, total_tracks)
    log(f"[Lyrics] Target: {lyrics}")

    instrumental = audio.build_instrumental(stems, track_work, ffmpeg)
    reference = audio.build_reference(
        stems["vocals"], track_work, ffmpeg, cfg, original
    )

    melody_data = melody.extract_melody(
        cfg=cfg,
        vocals=stems["vocals"],
        track_work=track_work,
    )

    guide, repeat_plan = diffsinger.render_ukrainian_guide(
        cfg=cfg,
        lyrics_path=lyrics,
        melody=melody_data,
        asr=source_asr,
        track_work=track_work,
    )

    final_instrumental = audio.extend_instrumental_for_repeat(
        instrumental=instrumental,
        repeat_plan=repeat_plan,
        track_work=track_work,
        ffmpeg=ffmpeg,
    )

    if cfg.get("write_guide_preview", True):
        audio.render_guide_preview(
            cfg,
            original,
            final_instrumental,
            guide,
            track_work,
            ffmpeg,
            OUT_DIR,
        )

    if cfg.get("seed_vc_enabled", True):
        converted = seed.convert(
            cfg=cfg,
            guides=[guide],
            reference=reference,
            track_work=track_work,
        )
        return audio.render_final(
            cfg,
            original,
            final_instrumental,
            converted,
            track_work,
            ffmpeg,
            OUT_DIR,
        )

    log("[Seed-VC] Disabled; final output uses the DiffSinger guide timbre.")
    return audio.render_final(
        cfg,
        original,
        final_instrumental,
        [guide],
        track_work,
        ffmpeg,
        OUT_DIR,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Only extract source lyrics/timings from input MP3 files.",
    )
    args = parser.parse_args()

    for path in (LOG_DIR, OUT_DIR, WORK_DIR):
        path.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")

    cfg = apply_gpu_profile(load_config())
    originals = find_input_mp3s()
    ffmpeg = find_ffmpeg()
    require(SEED_DIR, "Seed-VC runtime")

    log(f"=== Vocal Translate AI v{__version__}: Ukrainian DiffSinger ===")
    log(f"Tracks found: {len(originals)}")
    log(f"Mode: {'extract-only' if args.extract_only else 'full pipeline'}")

    successes: list[tuple[Path, list[Path]]] = []
    failures: list[tuple[Path, Exception]] = []

    for index, original in enumerate(originals, 1):
        log(f"\n[{index}/{len(originals)}] {original.name}")
        try:
            outputs = process_track(
                original=original,
                cfg=cfg,
                ffmpeg=ffmpeg,
                total_tracks=len(originals),
                extract_only=args.extract_only,
            )
            successes.append((original, outputs))
        except Exception as exc:
            failures.append((original, exc))
            log(f"TRACK FAILED: {original.name}: {exc}")
            if cfg.get("batch_stop_on_error", False):
                raise

    log("\n=== BATCH SUMMARY ===")
    for original, outputs in successes:
        if args.extract_only:
            log(f"OK:   {original.name} -> source lyrics exported")
        else:
            for output in outputs:
                log(f"OK:   {original.name} -> {output.name}")
    for original, exc in failures:
        log(f"FAIL: {original.name} -> {exc}")

    if failures and not successes:
        return 2
    if failures:
        log(f"Completed with issues: {len(successes)} success, {len(failures)} failed.")
    else:
        log(f"Completed successfully: {len(successes)} track(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("Cancelled by user.")
        raise SystemExit(130)
    except Exception as exc:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log(f"\nFATAL ERROR: {exc}")
        with RUN_LOG.open("a", encoding="utf-8") as fh:
            fh.write("\n=== TRACEBACK ===\n")
            traceback.print_exc(file=fh)
        print("Detailed logs are in the logs folder.", file=sys.stderr)
        raise SystemExit(1)
