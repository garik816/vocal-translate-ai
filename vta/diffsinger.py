from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import LOG_DIR, ROOT, log, run_logged, require
from .ustx import build_ustx

HEADLESS_PROJECT = ROOT / "tools" / "OpenUtauHeadless" / "OpenUtauHeadless.csproj"
HEADLESS_DLL = (
    ROOT
    / "tools"
    / "OpenUtauHeadless"
    / "bin"
    / "Release"
    / "net10.0"
    / "OpenUtauHeadless.dll"
)
SINGERS_ROOT = ROOT / "runtime" / "diffsinger"


def render_ukrainian_guide(
    cfg: dict[str, Any],
    lyrics_path: Path,
    melody: dict[str, Any],
    asr: dict[str, Any] | None,
    track_work: Path,
) -> tuple[Path, dict[str, Any] | None]:
    require(HEADLESS_DLL, "OpenUtau headless renderer")

    ustx_path, repeat_plan = build_ustx(
        cfg=cfg,
        lyrics_path=lyrics_path,
        melody=melody,
        asr=asr,
        track_work=track_work,
    )

    out_dir = track_work / "diffsinger"
    guide = out_dir / "guide_uk.wav"
    singer_hint = str(cfg.get("diffsinger_singer", "Nero"))
    steps = int(cfg.get("diffsinger_steps", 30))

    cmd = [
        "dotnet",
        str(HEADLESS_DLL),
        str(ustx_path),
        str(guide),
        str(SINGERS_ROOT),
        singer_hint,
        str(steps),
    ]

    log(
        f"[DiffSinger] Rendering Ukrainian guide with singer={singer_hint}, "
        f"steps={steps}..."
    )
    run_logged(
        cmd,
        LOG_DIR / "diffsinger.log",
        cwd=ROOT,
        check=True,
    )

    if not guide.exists() or guide.stat().st_size < 4096:
        raise RuntimeError("DiffSinger did not create a valid guide WAV.")

    log(f"[DiffSinger] Ukrainian guide: {guide}")
    return guide, repeat_plan
