"""9:16 dikey taslak render (FFmpeg). Sabit merkez kırpma; ses seviyesi değiştirilmez."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

from .config import RenderConfig

log = logging.getLogger("umbrella_vea.render")


class RenderError(RuntimeError):
    pass


def crop_filter(width: int, height: int) -> str:
    """Kaynağı merkezden hedef en-boy oranına kırpar, sonra hedef çözünürlüğe ölçekler.
    Yüz takibi yapılmaz. FFmpeg döndürme (rotation) metaverisini kırpmadan önce otomatik uygular."""
    ar = f"{width}/{height}"
    return (
        f"crop='trunc(min(iw,ih*{ar})/2)*2':'trunc(min(ih,iw/({ar}))/2)*2',"
        f"scale={width}:{height}:flags=lanczos,setsar=1"
    )


def _subtitle_filter(srt_name: str, cfg: RenderConfig) -> str:
    style = (
        f"FontName={cfg.subtitle_font},FontSize={cfg.subtitle_font_size},PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=60"
    )
    # srt_name çalışma klasörüne göreli ve yalnızca güvenli karakterler içerir; kaçış sorunu olmaz.
    return f"subtitles={srt_name}:charenc=UTF-8:force_style='{style}'"


_OUT_TIME = re.compile(r"out_time_(?:ms|us)=(\d+)")


def render_clip(source: Path, start: float, end: float, dest: Path, cfg: RenderConfig, has_audio: bool,
                srt_path: Path | None = None, progress: Callable[[float], None] | None = None) -> Path:
    """[start, end] aralığını 1080x1920 MP4 olarak dışa aktarır. Orijinal dosya yalnızca okunur."""
    duration = max(0.05, end - start)
    vf = crop_filter(cfg.width, cfg.height)
    if srt_path is not None:
        if srt_path.parent != dest.parent:
            raise RenderError("SRT dosyası çıktı klasöründe olmalı")
        vf += "," + _subtitle_filter(srt_path.name, cfg)
    tmp = dest.with_name(dest.stem + ".part.mp4")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", f"{start:.3f}", "-i", str(source.resolve()), "-t", f"{duration:.3f}",
        "-map", "0:v:0", "-vf", vf,
        "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf), "-pix_fmt", "yuv420p",
    ]
    if has_audio:
        # Ses filtresi yok: seviye korunur; yalnızca AAC'ye yeniden kodlanır.
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", cfg.audio_bitrate]
    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", tmp.name]

    # stderr geçici dosyaya yazılır: boru dolup FFmpeg'in kilitlenmesi (deadlock) önlenir.
    # Zamanlayıcı, FFmpeg hiç çıktı vermeden takılsa bile süreci sonlandırır.
    timeout = max(120.0, duration * 20)
    with tempfile.TemporaryFile(mode="w+") as errf:
        proc = subprocess.Popen(cmd, cwd=str(dest.parent), stdout=subprocess.PIPE, stderr=errf, text=True)
        assert proc.stdout is not None
        timed_out = threading.Event()

        def _kill() -> None:
            timed_out.set()
            proc.kill()

        watchdog = threading.Timer(timeout, _kill)
        watchdog.start()
        try:
            for line in proc.stdout:
                m = _OUT_TIME.match(line.strip())
                if m and progress:
                    progress(min(1.0, int(m.group(1)) / 1_000_000 / duration))
            rc = proc.wait()
        finally:
            watchdog.cancel()
        errf.seek(0)
        stderr = errf.read()
    if timed_out.is_set():
        tmp.unlink(missing_ok=True)
        raise RenderError(f"FFmpeg render zaman aşımına uğradı ({timeout:.0f} sn)")
    if rc != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RenderError(f"FFmpeg render başarısız: {stderr.strip()[-800:] or 'çıktı üretilmedi'}")
    tmp.replace(dest)
    return dest
