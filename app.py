from __future__ import annotations

import sys
import traceback

from vta import ace, audio, seed
from vta.common import (ACE_DIR, LOG_DIR, OUT_DIR, RUN_LOG, SEED_DIR, WORK_DIR,
                        find_ffmpeg, find_input_mp3s, find_lyrics_for,
                        load_config, log, require)
from vta.net import start_ace_server, stop_process_tree


def process_track(original, lyrics, cfg, ffmpeg, base_url):
    track_work = WORK_DIR / original.stem
    track_work.mkdir(parents=True, exist_ok=True)
    log("")
    log("=" * 68)
    log(f"TRACK: {original.name}")
    log(f"Lyrics: {lyrics}")
    log("=" * 68)

    stems = audio.run_demucs(original, track_work, cfg)
    instrumental = audio.build_instrumental(stems, track_work, ffmpeg)
    reference = audio.build_reference(
        stems["vocals"], track_work, ffmpeg, cfg, original
    )
    guides = ace.generate(
        cfg, base_url, stems["vocals"], reference, lyrics, track_work
    )
    converted = seed.convert(cfg, guides, reference, track_work)
    return audio.render_final(
        cfg, original, instrumental, converted, track_work, ffmpeg, OUT_DIR
    )


def main() -> int:
    for path in (LOG_DIR, OUT_DIR, WORK_DIR):
        path.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")

    cfg = load_config()
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

    log("=== Vocal Translate AI v4 batch ===")
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
    proc = None
    started = False
    successes = []
    failures = []

    try:
        proc, started = start_ace_server(port)
        for index, (original, lyrics) in enumerate(valid_jobs, 1):
            log(f"\n[{index}/{len(valid_jobs)}] Starting {original.name}")
            try:
                outputs = process_track(original, lyrics, cfg, ffmpeg, base_url)
                successes.append((original, outputs))
            except Exception as exc:
                failures.append((original, exc))
                log(f"TRACK FAILED: {original.name}: {exc}")
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
        log(f"Completed with issues: {len(successes)} success, "
            f"{len(failures)} failed, {len(skipped)} skipped.")
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
