"""Türkçe konuşmayı zaman kodlu metne çevirir (faster-whisper, tamamen yerel)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .config import TranscribeConfig

log = logging.getLogger("umbrella_vea.transcribe")


@dataclass
class Word:
    start: float
    end: float
    text: str
    probability: float


@dataclass
class Segment:
    start: float
    end: float
    text: str
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    language: str
    language_probability: float
    duration_sec: float
    engine: str
    model: str
    segments: list[Segment]

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments).strip()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Transcript":
        segs = [Segment(**{**s, "words": [Word(**w) for w in s.get("words", [])]}) for s in d["segments"]]
        return cls(**{**d, "segments": segs})


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, duration: float, progress: Callable[[float], None] | None = None) -> Transcript: ...


class TranscriptionUnavailable(RuntimeError):
    """Model yüklenemedi (ör. ilk indirme için internet yok)."""


class FasterWhisperTranscriber:
    def __init__(self, cfg: TranscribeConfig):
        self.cfg = cfg
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:  # pragma: no cover - kurulum hatası
                raise TranscriptionUnavailable("faster-whisper kurulu değil: pip install -r requirements.txt") from exc
            log.info("Whisper modeli yükleniyor: %s (%s, %s)", self.cfg.model, self.cfg.device, self.cfg.compute_type)
            try:
                self._model = WhisperModel(
                    self.cfg.model,
                    device=self.cfg.device,
                    compute_type=self.cfg.compute_type,
                    download_root=self.cfg.download_root or None,
                )
            except Exception as exc:
                raise TranscriptionUnavailable(
                    f"Whisper modeli '{self.cfg.model}' yüklenemedi: {exc}. İlk çalıştırmada model "
                    "internetten indirilir; çevrimdışıysanız modeli önceden indirip 'model' alanına klasör yolunu yazın."
                ) from exc
        return self._model

    def transcribe(self, audio_path: Path, duration: float, progress: Callable[[float], None] | None = None) -> Transcript:
        model = self._load()
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=self.cfg.language or None,
            beam_size=self.cfg.beam_size,
            word_timestamps=True,
            vad_filter=self.cfg.vad_filter,
            condition_on_previous_text=False,
        )
        segments: list[Segment] = []
        for s in segments_iter:  # üreteç: transkripsiyon ilerledikçe segment gelir
            text = s.text.strip()
            if not text:
                continue
            words = [Word(round(w.start, 3), round(w.end, 3), w.word.strip(), round(w.probability, 3)) for w in (s.words or [])]
            segments.append(Segment(round(s.start, 3), round(s.end, 3), text, round(s.avg_logprob, 3), round(s.no_speech_prob, 3), words))
            if progress and duration > 0:
                progress(min(1.0, s.end / duration))
        return Transcript(
            language=info.language,
            language_probability=round(info.language_probability, 3),
            duration_sec=round(duration, 3),
            engine="faster-whisper",
            model=self.cfg.model,
            segments=segments,
        )
