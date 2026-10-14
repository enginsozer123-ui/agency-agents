"""Videoya işlenecek altyazılar (ASS). SRT tek başına kelime vurgusu taşıyamadığı için
dinamik stil ASS (Advanced SubStation Alpha) ile üretilir ve FFmpeg/libass ile işlenir.

dynamic_highlight: Ekranda birkaç kelimelik satır görünür. O an konuşulan kelime gerçek kelime
zamanlarına göre renklenir ve hafifçe büyür. plain: satır tek renk, segment zamanlı.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import CaptionsConfig
from .srt import Cue, segments_to_cues
from .transcribe import Segment, Word

SENTENCE_END = (".", "?", "!", "…")
CHUNK_GAP_SEC = 0.6  # kelimeler arası bu kadar boşluk varsa yeni satır
LAST_WORD_HOLD_SEC = 0.25


@dataclass
class AssEvent:
    start: float
    end: float
    text: str  # ASS biçim kodlarını içerebilir


def ass_ts(sec: float) -> str:
    cs = max(0, int(round(sec * 100)))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _hex_to_ass(color: str) -> str:
    r, g, b = color[1:3], color[3:5], color[5:7]
    return f"&H00{b}{g}{r}&".upper()


def _escape(text: str) -> str:
    # ASS'te { } biçim kodu, \ kaçış karakteridir; konuşma metninde düz metin olarak kalmalı.
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def chunk_words(words: list[Word], max_words: int) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur and (len(cur) >= max_words or w.start - cur[-1].end > CHUNK_GAP_SEC):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w.text.endswith(SENTENCE_END):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def word_highlight_events(segments: list[Segment], cfg: CaptionsConfig) -> list[AssEvent]:
    """Her kelime için bir olay: satırın tamamı görünür, aktif kelime vurgulanır.
    Zamanlar doğrudan transkriptin kelime zamanlarıdır (kurgu sonrası zaman çizelgesine taşınmış)."""
    words = [w for s in segments for w in s.words]
    color = _hex_to_ass(cfg.highlight_color)
    events: list[AssEvent] = []
    chunks = chunk_words(words, cfg.max_words_per_line)
    for ci, chunk in enumerate(chunks):
        next_chunk_start = chunks[ci + 1][0].start if ci + 1 < len(chunks) else None
        for i, w in enumerate(chunk):
            start = w.start
            if i + 1 < len(chunk):
                end = chunk[i + 1].start
            else:
                end = w.end + LAST_WORD_HOLD_SEC
                if next_chunk_start is not None:
                    end = min(end, next_chunk_start)
            end = max(end, start + 0.05)
            parts = []
            for j, x in enumerate(chunk):
                t = _escape(x.text)
                if j == i and cfg.highlight_active_word:
                    parts.append(f"{{\\c{color}\\fscx112\\fscy112}}{t}{{\\r}}")
                else:
                    parts.append(t)
            events.append(AssEvent(start, end, " ".join(parts)))
    return events


def plain_events(cues: list[Cue]) -> list[AssEvent]:
    return [AssEvent(c.start, max(c.end, c.start + 0.2), _escape(c.text).replace("\n", "\\N")) for c in cues]


def build_events(segments: list[Segment], cfg: CaptionsConfig) -> tuple[list[AssEvent], str]:
    """(olaylar, gerçekleşen_zamanlama) döndürür. Kelime zamanı yoksa segment zamanına düşer ve bunu bildirir."""
    has_words = any(s.words for s in segments)
    if cfg.timing == "word_level" and has_words:
        if cfg.style == "dynamic_highlight":
            return word_highlight_events(segments, cfg), "word_level"
        words = [w for s in segments for w in s.words]
        return [AssEvent(c[0].start, c[-1].end + LAST_WORD_HOLD_SEC, _escape(" ".join(w.text for w in c)))
                for c in chunk_words(words, cfg.max_words_per_line)], "word_level"
    return plain_events(segments_to_cues(segments)), "segment"


def to_ass(events: list[AssEvent], cfg: CaptionsConfig, width: int, height: int) -> str:
    margin_v = int(height * 0.2)  # Reels/Shorts arayüzü alt kısmı kapatır; metin biraz yukarıda
    outline = max(2, cfg.font_size // 14)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{cfg.font},{cfg.font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{outline},0,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 0,{ass_ts(e.start)},{ass_ts(e.end)},Default,,0,0,0,,{e.text}" for e in events]
    return header + "\n".join(lines) + "\n"
