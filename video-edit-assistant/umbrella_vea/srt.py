"""SRT üretimi. Uzun segmentleri kelime zamanlarına göre okunabilir parçalara böler."""

from __future__ import annotations

from dataclasses import dataclass

from .transcribe import Segment

MAX_CHARS = 42  # satır başına
MAX_LINES = 2
MAX_CUE_SEC = 6.0


@dataclass
class Cue:
    start: float
    end: float
    text: str


def fmt_ts(sec: float) -> str:
    ms = max(0, int(round(sec * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _wrap(text: str) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > MAX_CHARS:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    if len(lines) > MAX_LINES:  # sığmazsa son satırda birleştir (kelime kaybetme)
        lines = lines[: MAX_LINES - 1] + [" ".join(lines[MAX_LINES - 1:])]
    return "\n".join(lines)


def segments_to_cues(segments: list[Segment]) -> list[Cue]:
    cues: list[Cue] = []
    limit = MAX_CHARS * MAX_LINES
    for seg in segments:
        if not seg.words or (len(seg.text) <= limit and seg.end - seg.start <= MAX_CUE_SEC):
            cues.append(Cue(seg.start, seg.end, seg.text))
            continue
        buf: list = []
        for w in seg.words:
            candidate = " ".join(x.text for x in buf + [w])
            if buf and (len(candidate) > limit or w.end - buf[0].start > MAX_CUE_SEC):
                cues.append(Cue(buf[0].start, buf[-1].end, " ".join(x.text for x in buf)))
                buf = []
            buf.append(w)
        if buf:
            cues.append(Cue(buf[0].start, buf[-1].end, " ".join(x.text for x in buf)))
    return cues


def clip_cues(cues: list[Cue], start: float, end: float) -> list[Cue]:
    """[start, end] aralığına düşen altyazıları kesite göre (0'dan başlayarak) kaydırır."""
    out = []
    for c in cues:
        if c.end <= start or c.start >= end:
            continue
        out.append(Cue(max(c.start, start) - start, min(c.end, end) - start, c.text))
    return out


def to_srt(cues: list[Cue]) -> str:
    blocks = []
    for i, c in enumerate(cues, 1):
        end = max(c.end, c.start + 0.2)
        blocks.append(f"{i}\n{fmt_ts(c.start)} --> {fmt_ts(end)}\n{_wrap(c.text)}\n")
    return "\n".join(blocks)
