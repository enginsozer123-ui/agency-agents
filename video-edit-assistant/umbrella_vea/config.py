"""Yapılandırma: kod varsayılanları < profil (umbrella_vea/profiles/<id>.toml) < kullanıcı TOML'u.

.env yalnızca gizli anahtarlar içindir.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

SUPPORTED_EXTENSIONS = (".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm")
PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
ZOOM_LEVELS = {"subtle": 1.08, "medium": 1.15}


class ConfigError(ValueError):
    """Yapılandırma eksik veya hatalı. Mesaj kullanıcıya olduğu gibi gösterilir."""


@dataclass
class ProfileConfig:
    id: str = ""  # boş: profil yok, yalnızca kod varsayılanları


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
    language: str = "tr"  # altyazı dili de budur
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 5
    vad_filter: bool = True
    download_root: str = ""  # boşsa faster-whisper varsayılan önbelleği


@dataclass
class AnalysisConfig:
    detect_scenes: bool = True
    scene_threshold: float = 27.0
    silence_noise_db: float = -35.0
    silence_min_sec: float = 0.6


@dataclass
class CandidateConfig:
    duration_mode: str = "fixed"  # "auto": [min_sec, max_sec] içinde içeriğe göre; "fixed": target_sec'e yakın
    min_sec: float = 6.0
    max_sec: float = 60.0
    target_sec: float = 30.0  # yalnızca duration_mode = "fixed"
    max_candidates: int = 5
    max_overlap: float = 0.3


@dataclass
class EditConfig:
    smart_cuts: bool = False  # sınırları cümle sonlarına ve yakın sahne geçişlerine hizala
    scene_snap_sec: float = 0.5
    remove_silence: bool = False  # kesit içindeki duraklamaları çıkar (jump cut)
    min_cut_silence_sec: float = 0.5  # bundan kısa duraklamalar korunur
    silence_padding_sec: float = 0.12  # kesilen sessizliğin iki yanında bırakılan pay
    punch_in_zooms: bool = False
    zoom_intensity: str = "subtle"  # subtle (%8) | medium (%15)


@dataclass
class RenderConfig:
    enabled: bool = True
    aspect_ratio: str = "9:16"
    width: int = 1080
    height: int = 1920
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: str = "192k"


@dataclass
class CaptionsConfig:
    enabled: bool = True
    timing: str = "segment"  # "segment" | "word_level"
    style: str = "plain"  # "plain" | "dynamic_highlight"
    highlight_active_word: bool = False
    burn: bool = True  # altyazıyı videoya işle
    export_srt: bool = True  # her kesit için ayrı .srt kaydet
    font: str = "Arial"
    font_size: int = 72  # 1080x1920 tuvalde piksel
    highlight_color: str = "#FFD400"
    max_words_per_line: int = 4


@dataclass
class AudioConfig:
    add_music: bool = False
    add_sound_effects: bool = False


@dataclass
class DeliveryConfig:
    preserve_original: bool = True
    auto_publish: bool = False
    require_review_before_delivery: bool = True
    delivery_dir_name: str = "delivery"


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
    profile: ProfileConfig = field(default_factory=ProfileConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    transcribe: TranscribeConfig = field(default_factory=TranscribeConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    candidates: CandidateConfig = field(default_factory=CandidateConfig)
    edit: EditConfig = field(default_factory=EditConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    captions: CaptionsConfig = field(default_factory=CaptionsConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    delivery: DeliveryConfig = field(default_factory=DeliveryConfig)
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

    @property
    def zoom_factor(self) -> float:
        return ZOOM_LEVELS[self.edit.zoom_intensity]


# Eski (v0.1) anahtarlar: anlaşılır taşıma mesajı verilir.
MOVED_KEYS = {
    ("render", "burn_subtitles"): "captions.burn",
    ("render", "write_srt"): "captions.export_srt",
    ("render", "subtitle_font"): "captions.font",
    ("render", "subtitle_font_size"): "captions.font_size",
}


def _apply(section: Any, values: dict[str, Any], name: str, source: str) -> None:
    if not isinstance(values, dict):
        raise ConfigError(f"{source}: [{name}] bir bölüm olmalı")
    known = {f.name: f for f in fields(section)}
    for key, value in values.items():
        if (name, key) in MOVED_KEYS:
            raise ConfigError(f"{source}: [{name}] {key} taşındı; bunun yerine {MOVED_KEYS[(name, key)]} kullanın.")
        if key not in known:
            raise ConfigError(f"{source}: bilinmeyen yapılandırma anahtarı [{name}] {key}")
        current = getattr(section, key)
        try:
            if isinstance(current, bool):
                if not isinstance(value, bool):
                    raise TypeError
            elif isinstance(current, Path):
                value = Path(value)
            elif isinstance(current, tuple):
                value = tuple(str(v).lower() for v in value)
            elif isinstance(current, float):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise TypeError
                value = float(value)
            elif isinstance(current, int):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise TypeError
            elif isinstance(current, str) and not isinstance(value, str):
                raise TypeError
        except TypeError:
            raise ConfigError(
                f"{source}: [{name}] {key} için {type(current).__name__} türünde değer bekleniyor, '{value}' verildi"
            ) from None
        setattr(section, key, value)


def _merge(cfg: Config, data: dict, source: str) -> None:
    for name, values in data.items():
        section = getattr(cfg, name, None)
        if name == "base_dir" or section is None or not is_dataclass(section):
            raise ConfigError(f"{source}: bilinmeyen yapılandırma bölümü [{name}]")
        _apply(section, values, name, source)


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: TOML sözdizimi hatası: {exc}") from None


def available_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.toml"))


def load_profile(cfg: Config, profile_id: str) -> None:
    path = PROFILES_DIR / f"{profile_id}.toml"
    if not path.is_file():
        raise ConfigError(f"Bilinmeyen profil '{profile_id}'. Mevcut profiller: {', '.join(available_profiles()) or 'yok'}")
    data = _read_toml(path)
    if "profile" in data or "paths" in data:
        raise ConfigError(f"{path}: profil dosyası [profile] veya [paths] içeremez")
    _merge(cfg, data, f"profil '{profile_id}'")
    cfg.profile.id = profile_id


def validate(cfg: Config) -> None:
    """Desteklenmeyen veya çelişen ayarları anlaşılır mesajla reddeder."""
    c, e, r, cap = cfg.candidates, cfg.edit, cfg.render, cfg.captions
    errors: list[str] = []
    if c.duration_mode not in ("auto", "fixed"):
        errors.append(f"candidates.duration_mode 'auto' veya 'fixed' olmalı (verilen: '{c.duration_mode}')")
    if not 0 < c.min_sec <= c.max_sec:
        errors.append(f"candidates.min_sec ({c.min_sec}) 0'dan büyük ve max_sec'ten ({c.max_sec}) küçük/eşit olmalı")
    if c.duration_mode == "fixed" and not c.min_sec <= c.target_sec <= c.max_sec:
        errors.append("candidates.target_sec, min_sec ile max_sec arasında olmalı")
    if e.zoom_intensity not in ZOOM_LEVELS:
        errors.append(f"edit.zoom_intensity şunlardan biri olmalı: {', '.join(ZOOM_LEVELS)} (verilen: '{e.zoom_intensity}')")
    try:
        aw, ah = (int(x) for x in r.aspect_ratio.split(":"))
        if r.width * ah != r.height * aw:
            errors.append(f"render.width x height ({r.width}x{r.height}) render.aspect_ratio ({r.aspect_ratio}) ile uyuşmuyor")
    except ValueError:
        errors.append(f"render.aspect_ratio 'G:Y' biçiminde olmalı (verilen: '{r.aspect_ratio}')")
    if r.width % 2 or r.height % 2 or r.width <= 0 or r.height <= 0:
        errors.append("render.width ve render.height pozitif çift sayı olmalı")
    if cap.timing not in ("segment", "word_level"):
        errors.append(f"captions.timing 'segment' veya 'word_level' olmalı (verilen: '{cap.timing}')")
    if cap.style not in ("plain", "dynamic_highlight"):
        errors.append(f"captions.style 'plain' veya 'dynamic_highlight' olmalı (verilen: '{cap.style}')")
    if cap.style == "dynamic_highlight" and cap.timing != "word_level":
        errors.append("captions.style = 'dynamic_highlight' için captions.timing = 'word_level' gerekir")
    if cap.highlight_active_word and cap.style != "dynamic_highlight":
        errors.append("captions.highlight_active_word = true için captions.style = 'dynamic_highlight' gerekir")
    if not (len(cap.highlight_color) == 7 and cap.highlight_color.startswith("#")):
        errors.append("captions.highlight_color '#RRGGBB' biçiminde olmalı")
    if cfg.audio.add_music:
        errors.append("audio.add_music = true henüz desteklenmiyor (müzik ekleme geliştirilmedi)")
    if cfg.audio.add_sound_effects:
        errors.append("audio.add_sound_effects = true henüz desteklenmiyor (ses efekti geliştirilmedi)")
    if cfg.delivery.auto_publish:
        errors.append("delivery.auto_publish = true desteklenmiyor: otomatik yayın özelliği yok ve bilerek eklenmedi")
    if not cfg.delivery.preserve_original:
        errors.append("delivery.preserve_original = false desteklenmiyor: orijinal dosya her zaman korunur")
    if errors:
        raise ConfigError("Yapılandırma hatası:\n  - " + "\n  - ".join(errors))


def load_config(path: str | Path | None = None, base_dir: str | Path | None = None,
                profile: str | None = None) -> Config:
    """Katmanlar: kod varsayılanları < profil < kullanıcı dosyası. `profile` verilirse dosyadakini ezer."""
    cfg = Config()
    user: dict = {}
    cfg_path = Path(path) if path else None
    if cfg_path is not None:
        if not cfg_path.exists():
            raise ConfigError(f"Yapılandırma dosyası bulunamadı: {cfg_path}")
        user = _read_toml(cfg_path)
        cfg.base_dir = cfg_path.resolve().parent
    profile_section = user.pop("profile", {})
    if not isinstance(profile_section, dict) or set(profile_section) - {"id"}:
        raise ConfigError(f"{cfg_path}: [profile] bölümünde yalnızca 'id' anahtarı olabilir")
    profile_id = profile or profile_section.get("id", "")
    if profile_id:
        load_profile(cfg, profile_id)
    _merge(cfg, user, str(cfg_path) if cfg_path else "yapılandırma")
    if base_dir is not None:
        cfg.base_dir = Path(base_dir).resolve()
    elif cfg_path is None:
        cfg.base_dir = Path.cwd()
    validate(cfg)
    return cfg


def load_env(base_dir: Path) -> None:
    """.env dosyasını ortam değişkenlerine yükler (varsa). Değerler asla loglanmaz."""
    env_file = base_dir / ".env"
    if env_file.exists():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
