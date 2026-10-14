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


def zoom_filter(width: int, height: int, zoom: float) -> str:
    """Merkezden hafif yakınlaştırma (punch-in): büyüt, sonra hedef boyuta merkezden kırp."""
    if zoom <= 1.0:
        return ""
    zw, zh = int(round(width * zoom / 2)) * 2, int(round(height * zoom / 2)) * 2
    return f",scale={zw}:{zh}:flags=lanczos,crop={width}:{height},setsar=1"


def build_filter_graph(pieces: list[tuple[float, float, float]], width: int, height: int, has_audio: bool,
                       ass_name: str | None) -> str:
    """pieces: (başlangıç, bitiş, zoom) — kesitin başına göre göreli saniye. Ses için yalnızca kesme
    (atrim) ve birleştirme (concat) yapılır; seviye/ton değiştiren filtre yoktur."""
    parts, labels = [], []
    for i, (a, b, z) in enumerate(pieces):
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,"
                     f"{crop_filter(width, height)}{zoom_filter(width, height, z)}[v{i}]")
        labels.append(f"[v{i}]")
        if has_audio:
            parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
            labels.append(f"[a{i}]")
    n = len(pieces)
    parts.append(f"{''.join(labels)}concat=n={n}:v=1:a={1 if has_audio else 0}[vc]{'[aout]' if has_audio else ''}")
    # ass_name çıktı klasörüne göreli, güvenli karakterli bir dosya adıdır (clip_01.ass).
    parts.append(f"[vc]ass={ass_name}[vout]" if ass_name else "[vc]null[vout]")
    return ";".join(parts)


_OUT_TIME = re.compile(r"out_time_(?:ms|us)=(\d+)")


def render_clip(source: Path, start: float, end: float, dest: Path, cfg: RenderConfig, has_audio: bool,
                ass_path: Path | None = None, progress: Callable[[float], None] | None = None,
                pieces: list[tuple[float, float, float]] | None = None) -> Path:
    """Kaynağın [start, end] aralığını hedef boyutta (ör. 1080x1920) MP4 olarak dışa aktarır.

    pieces: kaynak zamanlı (başlangıç, bitiş, zoom) parçaları; verilmezse tek parça. Parçalar arası
    boşluklar (kesilen sessizlikler) çıktıya girmez. Orijinal dosya yalnızca okunur."""
    pieces = pieces or [(start, end, 1.0)]
    rel = [(a - start, b - start, z) for a, b, z in pieces]
    duration = max(0.05, sum(b - a for a, b, _ in rel))
    if ass_path is not None and ass_path.parent != dest.parent:
        raise RenderError("Altyazı dosyası çıktı klasöründe olmalı")
    graph = build_filter_graph(rel, cfg.width, cfg.height, has_audio, ass_path.name if ass_path else None)
    tmp = dest.with_name(dest.stem + ".part.mp4")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(source.resolve()),
        "-filter_complex", graph, "-map", "[vout]",
        "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf), "-pix_fmt", "yuv420p",
    ]
    if has_audio:
        # Ses filtresi yok (yalnızca kesme/birleştirme): seviye korunur; AAC'ye yeniden kodlanır.
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", cfg.audio_bitrate]
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
