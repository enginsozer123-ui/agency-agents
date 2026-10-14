from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from umbrella_vea.config import Config, load_config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = load_config(ROOT / "config.example.toml", base_dir=tmp_path)
    c.watch.poll_interval_sec = 0.05
    c.watch.stable_checks = 2
    c.render.preset = "ultrafast"
    c.input_dir.mkdir(parents=True, exist_ok=True)
    return c


def make_video(dest: Path, seconds: float = 3.0, size: str = "640x360", audio: bool = True) -> Path:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s={size}:r=25:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(dest)]
    subprocess.run(cmd, check=True)
    return dest
