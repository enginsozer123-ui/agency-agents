"""Kural tabanlı kesit adayı seçimi.

Yalnızca transkriptte, sessizliklerde ve sahne geçişlerinde gerçekten bulunan sinyalleri kullanır.
Gerekçeler bu sinyallerden türetilir; içerikte olmayan bir şey yazılmaz.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field

from .config import CandidateConfig, EditConfig
from .edit import estimate_output_duration
from .transcribe import Segment

WEIGHTS = {"hook": 0.25, "qa": 0.25, "strong": 0.2, "boundary": 0.15, "duration": 0.15}

STRONG_PHRASES = (
    "kesinlikle", "asla", "mutlaka", "en önemli", "çok önemli", "önemli olan", "dikkat", "inanılmaz",
    "harika", "muhteşem", "sır", "sırrı", "hata", "hatası", "yanlış", "doğru olan", "gerçek şu ki",
    "aslında", "unutmayın", "bilmeniz gereken", "en büyük", "en iyi", "hiçbir zaman", "her zaman",
    "şaşırtıcı", "fark", "değiştirdi", "sonuç olarak", "kısacası", "özetle",
)
QUESTION_WORDS = (
    "neden", "niçin", "niye", "nasıl", "ne zaman", "nerede", "nereye", "hangi", "kaç", "kim", "ne kadar",
)
QUESTION_PARTICLE = re.compile(r"\b(m[ıiuü](s[ıiuü]n(ız|iz|uz|üz)?|y[ıiuü]z|d[ıiuü]r|ydı|ydi|ydu|ydü)?)\b")


def tr_lower(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


def is_question(text: str) -> bool:
    t = tr_lower(text).strip()
    if t.endswith("?"):
        return True
    padded = f" {re.sub(r'[^\w\s]', ' ', t)} "
    return bool(QUESTION_PARTICLE.search(t)) or any(f" {w} " in padded for w in QUESTION_WORDS)


def strong_hits(text: str) -> list[str]:
    t = f" {re.sub(r'[^\w\s!]', ' ', tr_lower(text))} "
    hits = [p for p in STRONG_PHRASES if f" {p} " in t]
    if "!" in text:
        hits.append("ünlem")
    return hits


@dataclass
class Candidate:
    id: str
    start: float
    end: float
    duration: float
    confidence: float
    basis: str  # "transcript" | "scenes" | "fallback"
    reasons: list[str]
    text: str = ""
    features: dict = field(default_factory=dict)
    output_duration_est: float = 0.0  # sessizlik kesildikten sonraki tahmini süre
    duration_note: str = ""  # süre hedefine neden uyulamadığı (varsa)

    def to_dict(self) -> dict:
        return asdict(self)


def _near(t: float, points: list[float], tol: float) -> bool:
    return any(abs(t - p) <= tol for p in points)


def _in_silence(t: float, silences: list[dict], tol: float = 0.25) -> bool:
    return any(s["start"] - tol <= t <= s["end"] + tol for s in silences)


def _quote(text: str, limit: int = 80) -> str:
    text = text.strip()
    return f"«{text if len(text) <= limit else text[: limit - 1] + '…'}»"


def _asr_quality(segs: list[Segment]) -> float:
    if not segs:
        return 0.0
    conf = sum(min(1.0, math.exp(s.avg_logprob)) for s in segs) / len(segs)
    speech = 1.0 - sum(s.no_speech_prob for s in segs) / len(segs)
    return max(0.3, min(1.0, conf)) * max(0.0, min(1.0, speech))


def _overlap(a: Candidate, b: Candidate) -> float:
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    return inter / max(1e-6, min(a.duration, b.duration))


def _select(pool: list[Candidate], cfg: CandidateConfig) -> list[Candidate]:
    chosen: list[Candidate] = []
    for c in sorted(pool, key=lambda c: (-c.confidence, c.start)):
        if all(_overlap(c, o) <= cfg.max_overlap for o in chosen):
            chosen.append(c)
        if len(chosen) >= cfg.max_candidates:
            break
    chosen.sort(key=lambda c: c.start)
    for i, c in enumerate(chosen, 1):
        c.id = f"clip_{i:02d}"
    return chosen


def _score_window(segs: list[Segment], i: int, j: int, duration: float, silences: list[dict],
                  cuts: list[float], cfg: CandidateConfig, edit: EditConfig | None = None) -> Candidate:
    window = segs[i : j + 1]
    prev_end = segs[i - 1].end if i > 0 else 0.0
    next_start = segs[j + 1].start if j + 1 < len(segs) else duration
    reasons: list[str] = []
    f: dict = {}
    smart = edit is None or edit.smart_cuts
    if smart:  # cümle sınırı + nefes payı; yakında sahne geçişi varsa ona hizala
        start = max(prev_end, window[0].start - 0.15, 0.0)
        end = min(next_start, window[-1].end + 0.3, duration)
        if edit is not None:
            snap_s = [c for c in cuts if prev_end <= c <= window[0].start and window[0].start - c <= edit.scene_snap_sec]
            snap_e = [c for c in cuts if window[-1].end <= c <= next_start and c - window[-1].end <= edit.scene_snap_sec]
            if snap_s:
                start = max(snap_s)
            if snap_e:
                end = min(snap_e)
            if snap_s or snap_e:
                reasons.append("Akıllı kesit: " + " ve ".join(
                    x for x, ok in (("başlangıç", snap_s), ("bitiş", snap_e)) if ok) + " yakındaki sahne geçişine hizalandı")
    else:  # akıllı kesit kapalı: tam segment sınırları, pay ve hizalama yok
        start, end = window[0].start, window[-1].end
    dur = end - start
    est = estimate_output_duration(start, end, silences, edit) if edit is not None else dur
    text = " ".join(s.text for s in window)

    gap_before = window[0].start - prev_end if i > 0 else window[0].start
    f["hook"] = 1.0 if (i == 0 or gap_before >= 1.0 or _near(window[0].start, cuts, 0.5)) else 0.0
    if f["hook"]:
        why = "konuşmanın ilk cümlesi" if i == 0 else (
            f"öncesinde {gap_before:.1f} sn sessizlik var" if gap_before >= 1.0 else "sahne geçişiyle başlıyor")
        reasons.append(f"Net giriş: {why}; açılış cümlesi {_quote(window[0].text, 60)}")

    # Soru-cevap: bir soru cümlesinden hemen sonra soru OLMAYAN bir cümle geliyorsa.
    qa = next(((window[k], window[k + 1]) for k in range(len(window) - 1)
               if is_question(window[k].text) and not is_question(window[k + 1].text)), None)
    f["qa"] = 1.0 if qa else 0.0
    if qa:
        reasons.append(f"Soru-cevap: soru {_quote(qa[0].text, 60)} ve hemen ardından gelen cümle {_quote(qa[1].text, 60)} aynı kesitte")

    hits = sorted({h for s in window for h in strong_hits(s.text)})
    f["strong"] = min(1.0, len(hits) / 2)
    if hits:
        reasons.append("Güçlü ifade(ler): " + ", ".join(f"'{h}'" for h in hits[:5]))

    gap_after = next_start - window[-1].end
    clean_end = gap_after >= 0.5 or _in_silence(window[-1].end, silences)
    clean_start = f["hook"] > 0 or gap_before >= 0.4 or _in_silence(window[0].start, silences)
    f["boundary"] = (0.5 if clean_start else 0.0) + (0.5 if clean_end else 0.0)
    if clean_end:
        reasons.append("Cümle sonunda ve bir duraklamada bitiyor (kesik kesilmiyor)")

    if cfg.duration_mode == "auto":
        f["duration"] = 1.0 if cfg.min_sec <= est <= cfg.max_sec else 0.0
        dur_reason = (f"Otomatik süre: kurgu sonrası tahmini {est:.1f} sn "
                      f"({cfg.min_sec:.0f}–{cfg.max_sec:.0f} sn hedef aralığı"
                      + (" içinde)" if f["duration"] else " dışında)"))
    else:
        f["duration"] = max(0.0, 1.0 - abs(est - cfg.target_sec) / cfg.target_sec)
        dur_reason = f"Süre {est:.1f} sn (hedef {cfg.target_sec:.0f} sn)"
    quality = _asr_quality(window)
    f["asr_quality"] = round(quality, 3)
    raw = sum(WEIGHTS[k] * f[k] for k in WEIGHTS)
    conf = round(raw * (0.5 + 0.5 * quality), 3)
    if not reasons:
        reasons.append("Bütün cümlelerden oluşan, uygun uzunlukta bir konuşma bölümü")
    reasons.append(f"{dur_reason}; transkript güven katsayısı {quality:.2f}")
    return Candidate("", round(start, 3), round(end, 3), round(dur, 3), conf, "transcript", reasons, text,
                     {k: round(v, 3) for k, v in f.items()}, round(est, 3))


def from_transcript(segs: list[Segment], duration: float, silences: list[dict], cuts: list[float],
                    cfg: CandidateConfig, edit: EditConfig | None = None) -> list[Candidate]:
    """Süre kontrolü kurgu SONRASI tahmini süreyle yapılır (sessizlik kesilecekse kısalır)."""
    if not segs:
        return []
    # sessizlik kesimi süreyi kısaltabileceği için ham süre üst sınırını biraz geniş tut
    span_limit = cfg.max_sec * (2.0 if edit is not None and edit.remove_silence else 1.0)
    windows: list[Candidate] = []
    for i in range(len(segs)):
        for j in range(i, len(segs)):
            if segs[j].end - segs[i].start > span_limit:
                break
            windows.append(_score_window(segs, i, j, duration, silences, cuts, cfg, edit))
    pool = [c for c in windows if cfg.min_sec <= c.output_duration_est <= cfg.max_sec]
    if not pool:
        shorter = [c for c in windows if c.output_duration_est < cfg.min_sec]
        if shorter:  # kaynak hedef alt sınırına yetecek kadar konuşma içermiyor
            longest = max(c.output_duration_est for c in shorter)
            pool = [c for c in shorter if c.output_duration_est >= longest - 1e-6]
            total_speech = sum(s.end - s.start for s in segs)
            for c in pool:
                c.duration_note = (f"Hedef süre {cfg.min_sec:.0f}–{cfg.max_sec:.0f} sn, ancak kaynakta bu aralığa uyan "
                                   f"bütün bir konuşma bölümü yok (toplam konuşma {total_speech:.1f} sn); "
                                   f"en uzun bütün bölüm seçildi ({c.output_duration_est:.1f} sn)")
                c.reasons.append(c.duration_note)
    if not pool:  # hiçbir pencere süre sınırlarına uymadı: en uzun tekil segment
        s = max(segs, key=lambda x: x.end - x.start)
        seg_len = s.end - s.start
        if seg_len > cfg.max_sec:
            fitting = [w for w in s.words if w.end <= s.start + cfg.max_sec]
            end = fitting[-1].end if fitting else s.start + cfg.max_sec
            text = " ".join(w.text for w in fitting) if fitting else s.text
            why = (f"En uzun konuşma bölümü ({seg_len:.1f} sn) azami süreyi ({cfg.max_sec:.0f} sn) aşıyor; "
                   + ("kelime sınırında kısaltıldı" if fitting else "kelime zamanı olmadığı için tam azami sürede kesildi"))
        else:
            end, text = s.end, s.text
            why = (f"Süre sınırlarına ({cfg.min_sec:.0f}–{cfg.max_sec:.0f} sn) uyan bir bölüm bulunamadı; "
                   f"en uzun tekil konuşma bölümü ({seg_len:.1f} sn) seçildi")
        pool.append(Candidate("", s.start, round(end, 3), round(end - s.start, 3), 0.2, "transcript", [why], text,
                              output_duration_est=round(end - s.start, 3), duration_note=why))
    return _select(pool, cfg)


def from_scenes(duration: float, cuts: list[float], cfg: CandidateConfig) -> list[Candidate]:
    """Konuşma yoksa: sahne sınırlarından aday üret (düşük güven, açıkça belirtilir)."""
    bounds = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
    pool: list[Candidate] = []
    for i in range(len(bounds) - 1):
        for j in range(i + 1, len(bounds)):
            span = bounds[j] - bounds[i]
            if span > cfg.max_sec:
                break
            if span >= min(cfg.min_sec, duration):
                fit = max(0.0, 1.0 - abs(span - cfg.target_sec) / cfg.target_sec)
                pool.append(Candidate("", round(bounds[i], 3), round(bounds[j], 3), round(span, 3), round(0.1 + 0.15 * fit, 3),
                                      "scenes", ["Konuşma algılanmadı; kesit sahne geçişi sınırlarına göre seçildi",
                                                 f"Süre {span:.1f} sn"], output_duration_est=round(span, 3)))
    if not pool:
        end = min(duration, cfg.max_sec)
        pool.append(Candidate("", 0.0, round(end, 3), round(end, 3), 0.1, "fallback",
                              ["Konuşma ve uygun sahne sınırı bulunamadı; videonun başından azami süre kadar alındı"],
                              output_duration_est=round(end, 3)))
    return _select(pool, cfg)
