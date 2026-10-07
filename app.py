from __future__ import annotations

import sys
import traceback

from vta import ace, audio, seed
from vta.common import (
    ACE_DIR, LOG_DIR, OUT_DIR, RUN_LOG, SEED_DIR, WORK_DIR,
    find_ffmpeg, find_input_mp3s, find_lyrics_for,
    load_config, log, require,
)
from vta.gpu import apply_gpu_profile
from vta.net import start_ace_server, stop_process_tree


def prepare_track(original, cfg, ffmpeg):
    track_work = WORK_DIR / original.stem
    track_work.mkdir(parents=True, exist_ok=True)
    stems = audio.run_demucs(original, track_work, cfg)
    instrumental = audio.build_instrumental(stems, track_work, ffmpeg)
    reference = audio.build_reference(
        stems["vocals"], track_work, ffmpeg, cfg, original
    )
    return track_work, stems, instrumental, reference


def main() -> int:
    for path in (LOG_DIR, OUT_DIR, WORK_DIR):
        path.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")

    cfg = apply_gpu_profile(load_config())
    originals = find_input_mp3s()
    ffmpeg = find_ffmpeg()
    require(ACE_DIR, "ACE-Step runtime")
    require(SEED_DIR, "Seed-VC runtime")

    jobs = []
    for original in originals:
        try:
            lyrics = find_lyrics_for(original, cfg, len(originals))
            jobs.append((original, lyrics))
        except Exception as exc:
            jobs.append((original, exc))

    log("=== Vocal Translate AI v6 strict quality batch ===")
    log(f"Tracks found: {len(originals)}")

    for original, item in jobs:
        if isinstance(item, Exception):
            log(f"  SKIP {original.name}: {item}")
        else:
            log(f"  OK   {original.name} -> {item.name}")

    valid_jobs = [(a, b) for a, b in jobs if not isinstance(b, Exception)]
    if not valid_jobs:
        raise RuntimeError("No valid jobs. Check MP3 names and matching lyric files.")

    port = int(cfg.get("api_port", 8001))
    base_url = f"http://127.0.0.1:{port}"
    release_between = bool(cfg.get("_release_gpu_between_stages", False))
    proc = None
    started = False
    successes = []
    failures = []

    try:
        if not release_between:
            proc, started = start_ace_server(port, str(cfg.get("ace_model", "acestep-v15-turbo")))

        for index, (original, lyrics) in enumerate(valid_jobs, 1):
            log("")
            log("=" * 68)
            log(f"[{index}/{len(valid_jobs)}] TRACK: {original.name}")
            log(f"Lyrics: {lyrics}")
            log("=" * 68)

            try:
                track_work, stems, instrumental, reference = prepare_track(
                    original, cfg, ffmpeg
                )

                source_mode = str(cfg.get("ace_source_mode", "delexicalized_mix")).lower()
                if source_mode == "full_mix":
                    ace_source = original
                elif source_mode == "vocals":
                    ace_source = stems["vocals"]
                elif source_mode == "delexicalized_mix":
                    ace_source = audio.build_cover_source(
                        stems, instrumental, track_work, ffmpeg, cfg
                    )
                else:
                    raise RuntimeError(f"Unknown ace_source_mode: {source_mode}")
                log(f"[ACE] Source mode: {source_mode} -> {ace_source.name}")

                if release_between:
                    log("[GPU] Starting ACE only for guide generation...")
                    proc, started = start_ace_server(port, str(cfg.get("ace_model", "acestep-v15-turbo")))

                guides = ace.generate(
                    cfg, base_url, ace_source, reference, lyrics, track_work
                )

                if release_between and started:
                    log("[GPU] Releasing ACE-Step VRAM before cleanup / Seed-VC...")
                    stop_process_tree(proc)
                    proc = None
                    started = False

                clean_guides = audio.isolate_guide_vocals(guides, track_work, cfg)

                if cfg.get("write_guide_preview", True) and clean_guides:
                    audio.render_guide_preview(
                        cfg, original, instrumental, clean_guides[0],
                        track_work, ffmpeg, OUT_DIR
                    )

                converted = seed.convert(cfg, clean_guides, reference, track_work)

                outputs = audio.render_final(
                    cfg, original, instrumental, converted,
                    track_work, ffmpeg, OUT_DIR
                )
                successes.append((original, outputs))

            except Exception as exc:
                failures.append((original, exc))
                log(f"TRACK FAILED: {original.name}: {exc}")

                if release_between and started:
                    stop_process_tree(proc)
                    proc = None
                    started = False

                if cfg.get("batch_stop_on_error", False):
                    raise

    finally:
        if started:
            stop_process_tree(proc)

    log("\n=== BATCH SUMMARY ===")
    for original, outputs in successes:
        for output in outputs:
            log(f"OK:   {original.name} -> {output.name}")
    for original, exc in failures:
        log(f"FAIL: {original.name} -> {exc}")

    skipped = [(a, b) for a, b in jobs if isinstance(b, Exception)]
    for original, exc in skipped:
        log(f"SKIP: {original.name} -> {exc}")

    if failures or skipped:
        log(
            f"Completed with issues: {len(successes)} success, "
            f"{len(failures)} failed, {len(skipped)} skipped."
        )
        return 2 if not successes else 0

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
