from __future__ import annotations

import sys
import traceback

from vta import ace, audio, seed
from vta.common import (ACE_DIR, LOG_DIR, OUT_DIR, RUN_LOG, SEED_DIR, WORK_DIR,
                        find_ffmpeg, find_input_mp3, find_lyrics, load_config, log, require)
from vta.net import start_ace_server, stop_process_tree


def main() -> int:
    for path in (LOG_DIR, OUT_DIR, WORK_DIR):
        path.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")
    cfg = load_config()
    original = find_input_mp3()
    lyrics = find_lyrics(cfg)
    ffmpeg = find_ffmpeg()
    require(ACE_DIR, "ACE-Step runtime")
    require(SEED_DIR, "Seed-VC runtime")
    track_work = WORK_DIR / original.stem
    track_work.mkdir(parents=True, exist_ok=True)
    log("=== Vocal Translate AI v4 ===")
    log(f"Input:  {original}")
    log(f"Lyrics: {lyrics}")
    stems = audio.run_demucs(original, track_work, cfg)
    instrumental = audio.build_instrumental(stems, track_work, ffmpeg)
    reference = audio.build_reference(stems["vocals"], track_work, ffmpeg, cfg)
    port = int(cfg.get("api_port", 8001))
    base_url = f"http://127.0.0.1:{port}"
    proc = None
    started = False
    try:
        proc, started = start_ace_server(port)
        guides = ace.generate(cfg, base_url, stems["vocals"], reference, lyrics, track_work)
        converted = seed.convert(cfg, guides, reference, track_work)
        outputs = audio.render_final(cfg, original, instrumental, converted, track_work, ffmpeg, OUT_DIR)
    finally:
        if started:
            stop_process_tree(proc)
    log("\nDONE")
    for output in outputs:
        log(f"  {output}")
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
