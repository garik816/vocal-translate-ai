from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import ROOT, log

PROFILE_PATH = ROOT / "runtime" / "gpu_profile.json"


def load_gpu_profile() -> dict[str, Any]:
    if not PROFILE_PATH.exists():
        return {
            "profile": "unknown",
            "name": "unknown",
            "memory_mb": 0,
            "legacy_torch": False,
            "low_vram": False,
        }
    try:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {
            "profile": "unknown",
            "name": "unknown",
            "memory_mb": 0,
            "legacy_torch": False,
            "low_vram": False,
        }


def apply_gpu_profile(cfg: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(cfg)
    profile = load_gpu_profile()
    cfg["_gpu_profile"] = profile

    name = str(profile.get("name", "unknown"))
    profile_name = str(profile.get("profile", "unknown"))
    memory_mb = int(profile.get("memory_mb") or 0)
    low_vram = bool(profile.get("low_vram", False))
    is_rtx3080 = bool(profile.get("rtx3080", False))
    release_between = bool(profile.get("release_between_stages", False))

    log(
        f"[GPU] {name} | profile={profile_name} | "
        f"VRAM={memory_mb} MB"
    )

    quality = str(cfg.get("quality_profile", "high")).lower()

    if low_vram:
        cfg["ace_model"] = "acestep-v15-turbo"
        cfg["ace_inference_steps"] = min(int(cfg.get("ace_inference_steps", 8)), 8)
        cfg["ace_batch_size"] = 1
        cfg["reference_seconds"] = min(
            int(cfg.get("reference_seconds", 22)),
            int(cfg.get("low_vram_reference_seconds", 12)),
        )
        cfg["seed_vc_diffusion_steps"] = min(
            int(cfg.get("seed_vc_diffusion_steps", 30)),
            int(cfg.get("low_vram_seed_vc_diffusion_steps", 20)),
        )
        cfg["_release_gpu_between_stages"] = True
        log(
            "[GPU] Low-VRAM mode: ACE DiT-only/Tier-1, batch=1, "
            "short reference, reduced Seed-VC steps, release VRAM between stages."
        )
    elif is_rtx3080:
        if quality in {"high", "strict"} and memory_mb >= 11264:
            cfg["ace_model"] = "acestep-v15-sft"
            cfg["ace_inference_steps"] = 50
        else:
            cfg["ace_model"] = "acestep-v15-turbo"
            cfg["ace_inference_steps"] = min(int(cfg.get("ace_inference_steps", 8)), 12)
        cfg["ace_batch_size"] = min(int(cfg.get("ace_batch_size", 1)), 1)
        cfg["_release_gpu_between_stages"] = True
        log(
            "[GPU] RTX 3080 mode: modern CUDA profile, batch=1, "
            "release ACE VRAM before Seed-VC."
        )
    elif release_between:
        if quality in {"high", "strict"} and memory_mb >= 14336:
            cfg["ace_model"] = "acestep-v15-sft"
            cfg["ace_inference_steps"] = 50
            log("[GPU] Quality mode: using ACE-Step SFT, 50 steps + CFG.")
        cfg["_release_gpu_between_stages"] = True
        log(
            "[GPU] Shared-VRAM safety: release ACE-Step before Seed-VC "
            "on this <=18GB GPU."
        )
    else:
        cfg["_release_gpu_between_stages"] = bool(
            cfg.get("release_gpu_between_stages", False)
        )

    return cfg


def ace_environment_overrides() -> dict[str, str]:
    profile = load_gpu_profile()
    env: dict[str, str] = {}
    if bool(profile.get("low_vram", False)):
        # ACE-Step officially supports MAX_CUDA_VRAM for its tier system.
        memory_mb = int(profile.get("memory_mb") or 4096)
        memory_gb = max(1, min(4, memory_mb // 1024))
        env["MAX_CUDA_VRAM"] = str(memory_gb)
        env["CUDA_MODULE_LOADING"] = "LAZY"
        env["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:64"
    elif bool(profile.get("rtx3080", False)):
        memory_mb = int(profile.get("memory_mb") or 10240)
        memory_gb = max(8, min(12, round(memory_mb / 1024)))
        env["MAX_CUDA_VRAM"] = str(memory_gb)
        env["CUDA_MODULE_LOADING"] = "LAZY"
        env["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:128"
    return env


def seed_environment_overrides() -> dict[str, str]:
    profile = load_gpu_profile()
    env: dict[str, str] = {}
    if bool(profile.get("low_vram", False)):
        env["CUDA_MODULE_LOADING"] = "LAZY"
        env["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:64"
    elif bool(profile.get("rtx3080", False)):
        env["CUDA_MODULE_LOADING"] = "LAZY"
        env["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:128"
    return env
