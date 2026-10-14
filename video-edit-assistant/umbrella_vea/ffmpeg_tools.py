"""FFmpeg/ffprobe yardımcıları: medya bilgisi, sessizlik tespiti, sürüm bilgisi."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path


class MediaError(RuntimeError):
    """Dosya okunamıyor, bozuk veya desteklenmiyor."""


def require_binaries() -> None:
    missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if missing:
        raise MediaError(f"Gerekli araç(lar) bulunamadı: {', '.join(missing)}. README'deki kurulum adımlarına bakın.")


def tool_version(binary: str) -> str:
    try:
        out = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=15).stdout
        return out.splitlines()[0] if out else "bilinmiyor"
    except (OSError, subprocess.SubprocessError):
        return "bulunamadı"


@dataclass
class MediaInfo:
    duration_sec: float
    width: int
    height: int
    fps: float
    video_codec: str
    rotation: int
    has_audio: bool
    audio_codec: str | None
    audio_channels: int | None
    audio_sample_rate: int | None
    container: str
    size_bytes: int

    @property
    def display_width(self) -> int:
        return self.height if self.rotation in (90, 270) else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation in (90, 270) else self.height

    def to_dict(self) -> dict:
        d = asdict(self)
        d["display_width"] = self.display_width
        d["display_height"] = self.display_height
        return d


def _fps(stream: dict) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = stream.get(key) or "0/0"
        try:
            num, den = raw.split("/")
            if int(den) != 0 and int(num) != 0:
                return round(float(Fraction(int(num), int(den))), 3)
        except (ValueError, ZeroDivisionError):
            continue
    return 0.0


def _rotation(stream: dict) -> int:
    rot = stream.get("tags", {}).get("rotate")
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = sd["rotation"]
    try:
        return int(float(rot)) % 360 if rot is not None else 0
    except ValueError:
        return 0


def probe(path: Path) -> MediaInfo:
    require_binaries()
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise MediaError("ffprobe zaman aşımına uğradı") from exc
    if proc.returncode != 0:
        raise MediaError(f"ffprobe dosyayı okuyamadı: {proc.stderr.strip()[:500] or 'bilinmeyen hata'}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise MediaError("Dosyada video akışı yok.")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or video.get("duration") or 0.0)
    if duration <= 0:
        raise MediaError("Video süresi okunamadı (dosya bozuk veya kopyalama tamamlanmamış olabilir).")
    return MediaInfo(
        duration_sec=round(duration, 3),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=_fps(video),
        video_codec=video.get("codec_name", "?"),
        rotation=_rotation(video),
        has_audio=audio is not None,
        audio_codec=audio.get("codec_name") if audio else None,
        audio_channels=int(audio["channels"]) if audio and audio.get("channels") else None,
        audio_sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        container=fmt.get("format_name", "?"),
        size_bytes=int(fmt.get("size") or path.stat().st_size),
    )


_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")


def parse_silencedetect(stderr: str, duration: float) -> list[dict]:
    silences: list[dict] = []
    start: float | None = None
    for line in stderr.splitlines():
        m = _SIL_START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _SIL_END.search(line)
        if m and start is not None:
            end = float(m.group(1))
            silences.append({"start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3)})
            start = None
    if start is not None and duration > start:  # dosya sessizlikle bitiyor
        silences.append({"start": round(start, 3), "end": round(duration, 3), "duration": round(duration - start, 3)})
    return silences


def detect_silences(path: Path, duration: float, noise_db: float, min_sec: float) -> list[dict]:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_sec}", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(120, int(duration * 2)))
    except subprocess.TimeoutExpired as exc:
        raise MediaError("Sessizlik analizi zaman aşımına uğradı") from exc
    if proc.returncode != 0:
        raise MediaError(f"Sessizlik analizi başarısız: {proc.stderr.strip()[-500:]}")
    return parse_silencedetect(proc.stderr, duration)


def extract_audio_wav(path: Path, dest: Path) -> Path:
    """Whisper için 16 kHz mono WAV üretir (yalnızca çalışma dosyası; orijinale dokunmaz)."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired as exc:
        raise MediaError("Ses çıkarma zaman aşımına uğradı") from exc
    if proc.returncode != 0:
        raise MediaError(f"Ses çıkarılamadı: {proc.stderr.strip()[-500:]}")
    return dest
