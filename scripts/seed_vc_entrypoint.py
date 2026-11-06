"""Reproducible launch for the external Seed-VC inference.py.

The upstream CLI has no --seed option. Seed PyTorch before the script initializes
its diffusion model, without modifying the downloaded Seed-VC checkout.
"""
from __future__ import annotations

import argparse
import os
import random
import runpy
import sys
from pathlib import Path


def run_inference(script: Path, seed: int, arguments: list[str]) -> None:
    if not script.is_file():
        raise FileNotFoundError(f"Seed-VC inference script does not exist: {script}")
    if seed < 0 or seed > (2**32 - 1):
        raise ValueError("Seed-VC random seed must be between 0 and 4294967295")

    # PYTHONHASHSEED must be set by the PARENT process before Python starts.
    random.seed(seed)
    import numpy as np
    import torch

    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True

    script = script.resolve()
    # Upstream inference.py imports its own modules relative to the runtime.
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script), *arguments]
    print(f"[Seed-VC] Reproducibility seed={seed}", flush=True)
    runpy.run_path(str(script), run_name="__main__")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run external Seed-VC inference.py with a reproducible seed")
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--seed", type=int, required=True)
    args, upstream_args = parser.parse_known_args()
    run_inference(args.script, args.seed, upstream_args)


if __name__ == "__main__":
    main()
