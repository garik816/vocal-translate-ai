from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .common import LOG_DIR, ROOT, SEED_DIR, child_env, log, require, run_logged, seed_python
from .gpu import seed_environment_overrides


def convert(cfg: dict[str, Any], guides: list[Path], reference: Path,
            track_work: Path) -> list[Path]:
    inference = SEED_DIR / "inference.py"
    require(inference, "Seed-VC inference.py")
    launcher = ROOT / "scripts" / "seed_vc_entrypoint.py"
    require(launcher, "Seed-VC deterministic launcher")
    base_seed = int(cfg.get("seed_vc_random_seed", 42))
    converted_dir = track_work / "seed_vc"
    if converted_dir.exists():
        shutil.rmtree(converted_dir)
    converted_dir.mkdir(parents=True, exist_ok=True)
    env = child_env()
    env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
    env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    env.update(seed_environment_overrides())
    outputs: list[Path] = []
    for index, guide in enumerate(guides, 1):
        work = converted_dir / f"candidate_{index:02d}"
        work.mkdir(parents=True, exist_ok=True)
        seed = base_seed + index - 1
        env["PYTHONHASHSEED"] = str(seed)
        cmd = [str(seed_python()), str(launcher), "--script", str(inference),
               "--seed", str(seed), "--source", str(guide),
               "--target", str(reference), "--output", str(work),
               "--diffusion-steps", str(cfg.get("seed_vc_diffusion_steps", 30)),
               "--length-adjust", "1.0", "--inference-cfg-rate",
               str(cfg.get("seed_vc_cfg_rate", 0.70)), "--f0-condition", "True",
               "--auto-f0-adjust", "False", "--semi-tone-shift",
               str(cfg.get("seed_vc_pitch_shift", 0)), "--fp16", "True"]
        log(f"[Seed-VC] Converting candidate {index}/{len(guides)} with seed={seed}...")
        run_logged(cmd, LOG_DIR / "seed_vc.log", cwd=SEED_DIR, env=env, check=True)
        generated = sorted(work.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not generated:
            raise RuntimeError(f"Seed-VC produced no WAV for candidate {index}.")
        final = converted_dir / f"converted_{index:02d}.wav"
        shutil.copy2(generated[0], final)
        outputs.append(final)
        log(f"[Seed-VC] Converted vocal: {final.name}")
    return outputs
