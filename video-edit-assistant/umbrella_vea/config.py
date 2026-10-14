"""Yapılandırma: TOML dosyası + .env (yalnızca gizli anahtarlar için)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

SUPPORTED_EXTENSIONS = (".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm")


@dataclass
class PathsConfig:
    input_dir: Path = Path("input")
    output_dir: Path = Path("output")
    state_dir: Path = Path("state")
    log_dir: Path = Path("logs")


@dataclass
class WatchConfig:
    poll_interval_sec: float = 2.0
    # Dosya boyutu ve değişiklik zamanı bu kadar ardışık kontrolde sabit kalmalı.
    stable_checks: int = 3
    extensions: tuple[str, ...] = SUPPORTED_EXTENSIONS
    max_attempts: int = 2


@dataclass
class TranscribeConfig:
    enabled: bool = True
    model: str = "small"  # tiny | base | small | medium | large-v3 veya yerel model klasörü
    language: str = "tr"
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 5
    vad_filter: bool = True
    download_root: str = ""  # boşsa faster-whisper varsayılan önbelleği


@dataclass
class AnalysisConfig:
    scene_threshold: float = 27.0
    silence_noise_db: float = -35.0
    silence_min_sec: float = 0.6


@dataclass
class CandidateConfig:
    min_sec: float = 6.0
    max_sec: float = 60.0
    target_sec: float = 30.0
    max_candidates: int = 5
    max_overlap: float = 0.3


@dataclass
class RenderConfig:
    enabled: bool = True
    width: int = 1080
    height: int = 1920
    burn_subtitles: bool = True
    write_srt: bool = True
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: str = "192k"
    subtitle_font: str = "Arial"
    subtitle_font_size: int = 14


@dataclass
class CloudConfig:
    enabled: bool = False
    # Transkript metninin buluta gönderilmesine açık onay. İkisi de true olmadan hiçbir şey gönderilmez.
    consent_send_transcript: bool = False
    provider: str = "anthropic"
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_transcript_chars: int = 20000
    timeout_sec: float = 120.0


@dataclass
class Config:
    paths: PathsConfig = field(default_factory=PathsConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    transcribe: TranscribeConfig = field(default_factory=TranscribeConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    candidates: CandidateConfig = field(default_factory=CandidateConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    cloud: CloudConfig = field(default_factory=CloudConfig)
    base_dir: Path = Path(".")

    def resolve(self, p: Path) -> Path:
        p = Path(os.path.expanduser(str(p)))
        return (p if p.is_absolute() else self.base_dir / p).resolve()  # sembolik bağlar da çözülür

    @property
    def input_dir(self) -> Path:
        return self.resolve(self.paths.input_dir)

    @property
    def output_dir(self) -> Path:
        return self.resolve(self.paths.output_dir)

    @property
    def state_dir(self) -> Path:
        return self.resolve(self.paths.state_dir)

    @property
    def log_dir(self) -> Path:
        return self.resolve(self.paths.log_dir)


def _apply(section: Any, values: dict[str, Any], name: str) -> None:
    known = {f.name: f for f in fields(section)}
    for key, value in values.items():
        if key not in known:
            raise ValueError(f"Bilinmeyen yapılandırma anahtarı: [{name}] {key}")
        current = getattr(section, key)
        if isinstance(current, Path):
            value = Path(value)
        elif isinstance(current, tuple):
            value = tuple(str(v).lower() for v in value)
        setattr(section, key, value)


def load_config(path: str | Path | None = None, base_dir: str | Path | None = None) -> Config:
    """TOML'u yükler. Dosya yoksa varsayılanlar kullanılır."""
    cfg = Config()
    cfg_path = Path(path) if path else None
    if cfg_path is not None:
        if not cfg_path.exists():
            raise FileNotFoundError(f"Yapılandırma dosyası bulunamadı: {cfg_path}")
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        for name, values in data.items():
            section = getattr(cfg, name, None)
            if section is None or not is_dataclass(section):
                raise ValueError(f"Bilinmeyen yapılandırma bölümü: [{name}]")
            _apply(section, values, name)
        cfg.base_dir = cfg_path.resolve().parent
    if base_dir is not None:
        cfg.base_dir = Path(base_dir).resolve()
    elif cfg_path is None:
        cfg.base_dir = Path.cwd()
    return cfg


def load_env(base_dir: Path) -> None:
    """.env dosyasını ortam değişkenlerine yükler (varsa). Değerler asla loglanmaz."""
    env_file = base_dir / ".env"
    if env_file.exists():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
