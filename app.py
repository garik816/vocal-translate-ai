from __future__ import annotations

import argparse
import shutil
import sys
import traceback
from pathlib import Path

from vta import __version__, ace, asr, audio, diffsinger, guide, melody, net, seed
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
    guide_only: bool,
    ace_only: bool = False,
    from_ace: bool = False,
    auto_guide: bool = False,
) -> list[Path]:
    track_work = WORK_DIR / original.stem
    track_work.mkdir(parents=True, exist_ok=True)

    log("")
    log("=" * 72)
    log(f"TRACK: {original.name}")
    log("=" * 72)

    mode = str(cfg.get("vocal_generation_mode", "external_guide")).strip().lower()
    if from_ace:
        mode = "external_guide"
    if mode not in {"ace_cover", "external_guide", "diffsinger"}:
        raise ValueError(
            "Invalid vocal_generation_mode: " + repr(mode)
            + "; choose ace_cover, external_guide, or diffsinger."
        )
    if ace_only:
        # Phase 1: only ACE-Step, no Demucs or Seed-VC in GPU memory.
        if str(cfg.get("ace_source_mode", "full_mix")).lower() != "full_mix":
            raise ValueError("--ace-only requires ace_source_mode=full_mix.")
        lyrics = find_lyrics_for(original, cfg, total_tracks)
        base_url = str(cfg.get("ace_api_url", "http://127.0.0.1:8001")).rstrip("/")
        if not net.health_ok(base_url):
            raise RuntimeError("ACE-Step API is not reachable at " + base_url +
                ". Start it with START_ACE_API.bat, then retry ACE_ONLY.bat.")
        covers = ace.generate(cfg, base_url, original, original, lyrics, track_work)
        for cover in covers:
            log(f"[ACE] Saved generated cover: {cover}")
        log("[Mode] ACE cover complete. Stop the ACE-Step API server " +
            "before processing it with --from-ace on a 12GB GPU.")
        return covers

    # Validate the vocal INPUT before invoking expensive Demucs/FFmpeg.
    # V8's GUIDE_ONLY.bat does not generate a cover: ACE_ONLY.bat must run first.
    external = None
    ace_mix_input = bool(from_ace)
    if not extract_only and mode == "external_guide":
        if from_ace:
            external = track_work / "ace_guides" / "guide_01.wav"
            if not external.is_file():
                raise FileNotFoundError(
                    "Missing ACE-Step cover for " + original.name + ": " + str(external)
                    + ". First start ACE-Step API and run ACE_ONLY.bat. "
                    "Then stop ACE-Step API and run FROM_ACE_GUIDE_ONLY.bat. "
                    "GUIDE_ONLY.bat alone does not generate a cover."
                )
        else:
            try:
                external = guide.find_external_guide(original, total_tracks)
            except FileNotFoundError as exc:
                cached_ace = track_work / "ace_guides" / "guide_01.wav"
                if auto_guide and cached_ace.is_file():
                    external = cached_ace
                    ace_mix_input = True
                    log("[Guide] No manual voice file; using cached ACE-Step cover.")
                else:
                    raise FileNotFoundError(
                        str(exc) + " First run START_ACE_API.bat in another "
                        "terminal and ACE_ONLY.bat to create the Ukrainian "
                        "cover; stop the ACE API; then run GUIDE_ONLY.bat or "
                        "RUN.bat. See V8_WORKFLOW.md."
                    ) from exc

    stems = audio.run_demucs(original, track_work, cfg)

    # Whisper/F0/USTX are *not* part of the ACE/guide-first synthesis modes.
    source_asr = None
    if extract_only or mode == "diffsinger":
        source_asr = asr.extract_source_lyrics(
            cfg=cfg,
            original=original,
            vocals=stems["vocals"],
            track_work=track_work,
        )
        if cfg.get("export_source_lyrics", True):
            export_source_lyrics(original, track_work)

    if extract_only:
        log("[Mode] Source lyrics exported; synthesis skipped.")
        return []

    instrumental = audio.build_instrumental(stems, track_work, ffmpeg)
    reference = audio.build_reference(
        stems["vocals"], track_work, ffmpeg, cfg, original
    )
    repeat_plan = None

    if mode == "diffsinger":
        # Retain v7 as an explicit legacy fallback for A/B comparisons.
        lyrics = find_lyrics_for(original, cfg, total_tracks)
        melody_data = melody.extract_melody(
            cfg=cfg, vocals=stems["vocals"], track_work=track_work
        )
        vocal, repeat_plan = diffsinger.render_ukrainian_guide(
            cfg=cfg, lyrics_path=lyrics, melody=melody_data,
            asr=source_asr, track_work=track_work,
        )
        final_instrumental = audio.extend_instrumental_for_repeat(
            instrumental, repeat_plan, track_work, ffmpeg,
        )
    elif mode == "ace_cover":
        lyrics = find_lyrics_for(original, cfg, total_tracks)
        base_url = str(cfg.get("ace_api_url", "http://127.0.0.1:8001")).rstrip("/")
        if not net.health_ok(base_url):
            raise RuntimeError(
                "ACE-Step 1.5 API is not running at " + base_url + ". "
                "Start ACE-Step using start_api_server.bat or 'uv run acestep-api'. "
                "See V8_WORKFLOW.md."
            )
        source_mode = str(cfg.get("ace_source_mode", "full_mix")).lower()
        if source_mode == "delexicalized_mix":
            cover_source = audio.build_cover_source(
                stems, instrumental, track_work, ffmpeg, cfg
            )
        elif source_mode == "full_mix":
            cover_source = original
        else:
            raise ValueError("ace_source_mode must be full_mix or delexicalized_mix")

        generated = ace.generate(
            cfg=cfg, base_url=base_url, source=cover_source,
            reference=reference, lyrics_path=lyrics, track_work=track_work,
        )
        if not bool(cfg.get("post_ace_demucs", True)):
            raise RuntimeError(
                "ACE-Step generates a FULL SONG, not isolated vocals. "
                "post_ace_demucs must be true to avoid mixing two instrumentals."
            )
        if len(generated) > 1:
            log(f"[ACE] Generated {len(generated)} candidates; using first. "
                "All candidates remain in work/<song>/ace_guides.")
        isolated = audio.isolate_guide_vocals([generated[0]], track_work, cfg)
        vocal = guide.prepare_guide(
            cfg, original, isolated[0], track_work, ffmpeg
        )
        final_instrumental = instrumental

    else:  # external_guide
        assert external is not None  # checked before loading Demucs
        log(f"[Guide] Using externally sung guide: {external}")
        if ace_mix_input or cfg.get("external_guide_is_mix", False):
            # Allows manually generated ACE-Step covers as input.
            external = audio.isolate_guide_vocals(
                [external], track_work, {**cfg, "post_ace_demucs": True}
            )[0]
        vocal = guide.prepare_guide(
            cfg, original, external, track_work, ffmpeg
        )
        final_instrumental = instrumental

    guide_preview = None
    if cfg.get("write_guide_preview", True) or guide_only:
        guide_preview = audio.render_guide_preview(
            cfg, original, final_instrumental, vocal,
            track_work, ffmpeg, OUT_DIR,
        )
    if guide_only:
        log("[Mode] Guide-only preview complete; Seed-VC skipped.")
        return [guide_preview] if guide_preview is not None else []

    if cfg.get("seed_vc_enabled", True):
        converted = seed.convert(
            cfg=cfg, guides=[vocal], reference=reference,
            track_work=track_work,
        )
        return audio.render_final(
            cfg, original, final_instrumental, converted,
            track_work, ffmpeg, OUT_DIR,
        )

    log("[Seed-VC] Disabled; final output uses the guide vocal.")
    return audio.render_final(
        cfg, original, final_instrumental, [vocal],
        track_work, ffmpeg, OUT_DIR,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Only extract source lyrics/timings from input MP3 files.",
    )
    parser.add_argument(
        "--ace-only", action="store_true",
        help="Generate ACE-Step covers only; requires local ACE API.",
    )
    parser.add_argument(
        "--from-ace", action="store_true",
        help="Use previous ACE cover from work/<song>/ace_guides/guide_01.wav.",
    )
    parser.add_argument(
        "--auto-guide", action="store_true",
        help="Use a manual sung guide if present, otherwise a cached ACE cover.",
    )
    parser.add_argument(
        "--guide-only",
        action="store_true",
        help="Render DiffSinger guide preview and stop before Seed-VC.",
    )
    args = parser.parse_args()
    if args.ace_only and (args.from_ace or args.guide_only or args.extract_only or args.auto_guide):
        parser.error("--ace-only cannot be combined with other modes.")

    for path in (LOG_DIR, OUT_DIR, WORK_DIR):
        path.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")

    cfg = apply_gpu_profile(load_config())
    originals = find_input_mp3s()
    ffmpeg = find_ffmpeg()
    require(SEED_DIR, "Seed-VC runtime")

    log(f"=== Vocal Translate AI v{__version__}: vocal-first pipeline ===")
    log(f"Tracks found: {len(originals)}")
    mode = (
        "extract-only"
        if args.extract_only
        else "ace-only"
        if args.ace_only
        else "guide-only"
        if args.guide_only
        else "full pipeline"
    )
    log(f"Mode: {mode}")

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
                guide_only=args.guide_only,
                ace_only=args.ace_only,
                from_ace=args.from_ace,
                auto_guide=args.auto_guide,
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
