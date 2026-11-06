"""The Seed-VC launcher must fix RNGs without modifying the upstream checkout.

Lightweight tests use fake torch/numpy modules and a dummy inference script.
They do NOT validate actual GPU determinism or audio quality.
"""
from __future__ import annotations

import random
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.seed_vc_entrypoint import run_inference


class SeedLauncherTests(unittest.TestCase):
    def test_fixed_seed_repeats_python_rng_and_forwards_arguments(self):
        calls = []
        np = types.ModuleType("numpy")
        np.random = types.SimpleNamespace(seed=lambda value: calls.append(("numpy", value)))
        torch = types.ModuleType("torch")
        torch.manual_seed = lambda value: calls.append(("torch", value))
        torch.cuda = types.SimpleNamespace(
            is_available=lambda: True,
            manual_seed_all=lambda value: calls.append(("cuda", value)),
        )
        torch.backends = types.SimpleNamespace(
            cudnn=types.SimpleNamespace(benchmark=True, deterministic=False)
        )

        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "fake_inference.py"
            script.write_text(
                "import random, sys, pathlib\n"
                "pathlib.Path(sys.argv[-1]).write_text("
                "str(random.random()) + ':' + sys.argv[-3])\n",
                encoding="utf-8",
            )
            output = Path(td) / "result.txt"
            with patch.object(sys, "argv", ["runner.py"]), \
                 patch.object(sys, "path", list(sys.path)), \
                 patch.dict(sys.modules, {"numpy": np, "torch": torch}):
                run_inference(script, 42, ["--source", "guide.wav", "--out", str(output)])
                first = output.read_text("utf-8")
                run_inference(script, 42, ["--source", "guide.wav", "--out", str(output)])
                second = output.read_text("utf-8")
            self.assertEqual(first, second)
            self.assertIn("guide.wav", first)
            self.assertEqual(calls.count(("torch", 42)), 2)
            self.assertEqual(calls.count(("numpy", 42)), 2)
            self.assertEqual(calls.count(("cuda", 42)), 2)
            self.assertTrue(torch.backends.cudnn.deterministic)
            self.assertFalse(torch.backends.cudnn.benchmark)

    def test_invalid_seed_rejected(self):
        with self.assertRaises(ValueError):
            run_inference(Path("dummy.py"), -1, [])
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "fake_inference.py"
            script.write_text("pass", encoding="utf-8")
            with self.assertRaises(ValueError):
                run_inference(script, 2**32, [])


if __name__ == "__main__":
    unittest.main()
