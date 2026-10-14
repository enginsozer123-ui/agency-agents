"""Kesit içi kurgu planı: sessizlik kesme (jump cut), punch-in parçaları ve kaynak→çıktı zaman eşlemesi.

Tüm kararlar gerçek ölçümlere dayanır: sessizlikler FFmpeg silencedetect, cümle başları transkript.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import EditConfig
from .transcribe import Segment, Word

MIN_PIECE_SEC = 0.15  # bundan kısa parçalar atılır (tek karelik "flash" önlenir)
MIN_ZOOM_PIECE_SEC = 1.0  # punch-in için parça en az bu kadar sürmeli


@dataclass
class Piece:
    src_start: float  # kaynak videodaki mutlak zaman
    src_end: float
    zoom: float = 1.0
    out_start: float = 0.0  # çıktı (kurgulanmış) zaman çizelgesindeki başlangıç

    @property
    def duration(self) -> float:
        return self.src_end - self.src_start

    def to_dict(self) -> dict:
        return {k: round(v, 3) for k, v in asdict(self).items()}


@dataclass
class EditPlan:
    pieces: list[Piece]
    removed_silences: list[dict]
    features: dict  # özellik -> {"status": ..., "detail": ...}

    @property
    def output_duration(self) -> float:
        return sum(p.duration for p in self.pieces)

    def to_out(self, t: float) -> float | None:
        """Kaynak zamanını çıktı zamanına çevirir; kesilen bir bölümdeyse None."""
        for p in self.pieces:
            if p.src_start - 1e-6 <= t <= p.src_end + 1e-6:
                return p.out_start + min(max(t, p.src_start), p.src_end) - p.src_start
        return None


def _status(status: str, detail: str) -> dict:
    return {"status": status, "detail": detail}


def silence_cuts(start: float, end: float, silences: list[dict], cfg: EditConfig) -> list[tuple[float, float]]:
    """[start, end] içinde kesilecek (kaynak zamanlı) aralıklar. Kenarlardaki pay korunur."""
    cuts = []
    for s in silences:
        if s["end"] - s["start"] < cfg.min_cut_silence_sec:
            continue
        a = max(start, s["start"] + cfg.silence_padding_sec)
        b = min(end, s["end"] - cfg.silence_padding_sec)
        if b - a > 0.05:
            cuts.append((a, b))
    return cuts


def estimate_output_duration(start: float, end: float, silences: list[dict], cfg: EditConfig) -> float:
    if not cfg.remove_silence:
        return end - start
    return (end - start) - sum(b - a for a, b in silence_cuts(start, end, silences, cfg))


def plan_clip(start: float, end: float, silences: list[dict], segments: list[Segment], cfg: EditConfig,
              zoom_factor: float, has_audio: bool) -> EditPlan:
    features: dict = {}
    keep = [(start, end)]
    removed: list[dict] = []

    if not cfg.remove_silence:
        features["remove_silence"] = _status("disabled", "Yapılandırmada kapalı.")
    elif not has_audio:
        features["remove_silence"] = _status("not_applicable", "Kaynakta ses yok; sessizlik analizi yapılamadı.")
    else:
        cuts = silence_cuts(start, end, silences, cfg)
        for a, b in cuts:
            nxt = []
            for ks, ke in keep:
                if b <= ks or a >= ke:
                    nxt.append((ks, ke))
                    continue
                if a > ks:
                    nxt.append((ks, a))
                if b < ke:
                    nxt.append((b, ke))
            keep = nxt
        keep = [(a, b) for a, b in keep if b - a >= MIN_PIECE_SEC] or [(start, end)]
        removed = [{"start": round(a, 3), "end": round(b, 3), "duration": round(b - a, 3)} for a, b in cuts]
        total = sum(r["duration"] for r in removed)
        features["remove_silence"] = (
            _status("applied", f"{len(removed)} duraklama kesildi, toplam {total:.2f} sn "
                               f"(≥ {cfg.min_cut_silence_sec} sn olanlar; iki yanda {cfg.silence_padding_sec} sn pay)")
            if removed else _status("not_applicable", f"Kesitte {cfg.min_cut_silence_sec} sn'den uzun duraklama yok.")
        )

    # punch-in: cümle başlarında parçalara böl, yakınlaştırmayı dönüşümlü uygula
    pieces = [Piece(a, b) for a, b in keep]
    if not cfg.punch_in_zooms:
        features["punch_in_zooms"] = _status("disabled", "Yapılandırmada kapalı.")
    else:
        starts = sorted(s.start for s in segments)
        split: list[Piece] = []
        for p in pieces:
            points = [t for t in starts if p.src_start + MIN_ZOOM_PIECE_SEC <= t <= p.src_end - MIN_ZOOM_PIECE_SEC]
            edges = [p.src_start, *points, p.src_end]
            split += [Piece(a, b) for a, b in zip(edges, edges[1:])]
        pieces = split
        if len(pieces) < 2:
            features["punch_in_zooms"] = _status(
                "not_applicable", "Kesitte geçiş noktası (jump cut veya cümle başı) yok; yakınlaştırma uygulanmadı.")
        else:
            for i, p in enumerate(pieces):
                p.zoom = zoom_factor if i % 2 else 1.0
            zoomed = sum(1 for p in pieces if p.zoom > 1)
            features["punch_in_zooms"] = _status(
                "applied", f"{len(pieces)} parçanın {zoomed} tanesinde %{round((zoom_factor - 1) * 100)} merkez "
                           "yakınlaştırma (geçişlerde dönüşümlü). Yüz takibi yok; merkez sabit.")

    t = 0.0
    for p in pieces:
        p.out_start = t
        t += p.duration
    return EditPlan(pieces, removed, features)


def map_segments(segments: list[Segment], plan: EditPlan) -> list[Segment]:
    """Transkripti kurgulanmış zaman çizelgesine taşır. Kesilen bölümlere düşen kelimeler atılır
    (bunlar sessizliktir); kesitin dışındaki segmentler alınmaz."""
    first, last = plan.pieces[0].src_start, plan.pieces[-1].src_end
    out: list[Segment] = []
    for seg in segments:
        if seg.end <= first or seg.start >= last:
            continue
        words: list[Word] = []
        for w in seg.words:
            mid = (w.start + w.end) / 2
            if plan.to_out(mid) is None:
                continue
            a = plan.to_out(max(w.start, first))
            b = plan.to_out(min(w.end, last))
            a = a if a is not None else plan.to_out(mid)
            b = b if b is not None else plan.to_out(mid)
            words.append(Word(round(a, 3), round(max(b, a + 0.05), 3), w.text, w.probability))
        if seg.words and not words:
            continue
        if words:
            s, e = words[0].start, words[-1].end
            text = " ".join(w.text for w in words)
        else:
            s = plan.to_out(max(seg.start, first))
            e = plan.to_out(min(seg.end, last))
            if s is None or e is None:
                continue
            text = seg.text
        out.append(Segment(round(s, 3), round(e, 3), text, seg.avg_logprob, seg.no_speech_prob, words))
    return out
