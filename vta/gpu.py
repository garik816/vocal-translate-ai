from __future__ import annotations

import json
from typing import Any

from .common import ROOT, log

PROFILE_PATH = ROOT / "runtime" / "gpu_profile.json"


def load_gpu_profile() -> dict[str, Any]:
    default = {
        "profile": "unknown",
        "name": "unknown",
        "memory_mb": 0,
        "legacy_torch": False,
        "low_vram": False,
        "rtx3080": False,
    }
    if not PROFILE_PATH.exists():
        return default
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8-sig"))
        return {**default, **data}
    except Exception:
        return default


def apply_gpu_profile(cfg: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(cfg)
    profile = load_gpu_profile()
    cfg["_gpu_profile"] = profile

    name = str(profile.get("name", "unknown"))
    profile_name = str(profile.get("profile", "unknown"))
    memory_mb = int(profile.get("memory_mb") or 0)
    low_vram = bool(profile.get("low_vram", False))
    is_rtx3080 = bool(profile.get("rtx3080", False))

    log(f"[GPU] {name} | profile={profile_name} | VRAM={memory_mb} MB")

    if low_vram:
        cfg["asr_device"] = "cpu"
        cfg["asr_model"] = str(cfg.get("low_vram_asr_model", "small"))
        cfg["diffsinger_steps"] = min(
            int(cfg.get("diffsinger_steps", 30)),
            int(cfg.get("low_vram_diffsinger_steps", 20)),
        )
        cfg["seed_vc_diffusion_steps"] = min(
            int(cfg.get("seed_vc_diffusion_steps", 50)),
            int(cfg.get("low_vram_seed_vc_diffusion_steps", 20)),
        )
        cfg["reference_seconds"] = min(int(cfg.get("reference_seconds", 8)), 8)
        log(
            "[GPU] Low-VRAM v7 mode: Whisper on CPU, lighter DiffSinger, "
            "reduced Seed-VC steps."
        )
    elif is_rtx3080:
        cfg["asr_device"] = "cuda"
        cfg["asr_model"] = str(cfg.get("asr_model", "turbo"))
        cfg["diffsinger_steps"] = min(int(cfg.get("diffsinger_steps", 30)), 30)
        cfg["seed_vc_diffusion_steps"] = min(
            int(cfg.get("seed_vc_diffusion_steps", 50)), 45
        )
        log("[GPU] RTX 3080 v7 mode: CUDA ASR + DirectML DiffSinger + Seed-VC.")
    else:
        if memory_mb >= 6144:
            cfg["asr_device"] = "cuda"
        else:
            cfg["asr_device"] = str(cfg.get("asr_device", "auto"))
        cfg["asr_model"] = str(cfg.get("asr_model", "turbo"))
        cfg["diffsinger_steps"] = int(cfg.get("diffsinger_steps", 30))
        log("[GPU] Modern v7 mode: CUDA ASR + DirectML DiffSinger + Seed-VC.")

    return cfg


def seed_environment_overrides() -> dict[str, str]:
    profile = load_gpu_profile()
    env: dict[str, str] = {"CUDA_MODULE_LOADING": "LAZY"}
    if bool(profile.get("low_vram", False)):
        env["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:64"
    elif bool(profile.get("rtx3080", False)):
        env["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:128"
    else:
        env["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:128"
    return env


# Legacy compatibility for the unused v6 ACE helpers.
def ace_environment_overrides() -> dict[str, str]:
    return {}
