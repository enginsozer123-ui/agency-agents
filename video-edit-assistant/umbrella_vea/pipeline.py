"""Tek bir videoyu uçtan uca işler: analiz → transkript → adaylar → 9:16 taslaklar → JSON rapor."""

from __future__ import annotations

import json
import logging
import math
import re
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path
from typing import Callable

from . import __version__, candidates as cand
from .cloud import semantic_scoring
from .config import Config
from .logging_setup import redact
from .ffmpeg_tools import MediaError, detect_silences, extract_audio_wav, probe, tool_version
from .captions import build_events, to_ass
from .edit import map_segments, plan_clip
from .render import RenderError, render_clip
from .scenes import detect_scene_cuts
from .srt import segments_to_cues, to_srt
from .transcribe import FasterWhisperTranscriber, Transcriber, Transcript

log = logging.getLogger("umbrella_vea.pipeline")

REPORT_SCHEMA_VERSION = 2


def aspect_ratio(w: int, h: int) -> str:
    g = math.gcd(w, h) or 1
    return f"{w // g}:{h // g}"


def _st(status: str, detail: str) -> dict:
    return {"status": status, "detail": detail}


def profile_summary(cfg: Config) -> dict:
    """Bu çalıştırmada gerçekten kullanılan (katmanlanmış) ayarların özeti."""
    c, e, r, cap = cfg.candidates, cfg.edit, cfg.render, cfg.captions
    return {
        "id": cfg.profile.id or None,
        "aspect_ratio": r.aspect_ratio, "width": r.width, "height": r.height,
        "duration_mode": c.duration_mode, "min_sec": c.min_sec, "max_sec": c.max_sec,
        "remove_silence": e.remove_silence, "detect_scenes": cfg.analysis.detect_scenes,
        "smart_cuts": e.smart_cuts, "punch_in_zooms": e.punch_in_zooms, "zoom_intensity": e.zoom_intensity,
        "captions": {"enabled": cap.enabled, "language": cfg.transcribe.language, "timing": cap.timing,
                     "style": cap.style, "highlight_active_word": cap.highlight_active_word,
                     "export_srt": cap.export_srt, "burn": cap.burn},
        "add_music": cfg.audio.add_music, "add_sound_effects": cfg.audio.add_sound_effects,
        "cloud_enabled": cfg.cloud.enabled, "preserve_original": cfg.delivery.preserve_original,
        "auto_publish": cfg.delivery.auto_publish,
        "require_review_before_delivery": cfg.delivery.require_review_before_delivery,
    }


class PartialRenderError(RenderError):
    """Bazı kesitler render edilemedi. Rapor yazılır; iş yeniden denenebilir kalır."""
Progress = Callable[[str, float], None]


def _versions() -> dict:
    out = {"umbrella_vea": __version__, "python": sys.version.split()[0],
           "ffmpeg": tool_version("ffmpeg"), "ffprobe": tool_version("ffprobe")}
    for pkg in ("faster-whisper", "ctranslate2", "scenedetect", "opencv-python", "anthropic"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = None
    return out


def output_dir_for(cfg: Config, source: Path, fp: str) -> Path:
    stem = re.sub(r"[^\w\-]+", "_", source.stem, flags=re.UNICODE).strip("_") or "video"
    return cfg.output_dir / f"{stem[:60]}_{fp[:8]}"


def _snapshot(p: Path) -> tuple[int, int]:
    st = p.stat()
    return st.st_size, st.st_mtime_ns


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(redact(json.dumps(data, ensure_ascii=False, indent=2)), encoding="utf-8")
    tmp.replace(path)


def process_video(source: Path, cfg: Config, fp: str, transcriber: Transcriber | None = None,
                  progress: Progress | None = None, cloud_client=None) -> tuple[Path, dict]:
    """Videoyu işler; (çıktı_klasörü, rapor) döndürür. Kritik hata olursa istisna fırlatır
    (rapor yine de 'failed' durumuyla yazılır)."""
    say = progress or (lambda stage, pct: None)
    source = source.resolve()
    before = _snapshot(source)
    out_dir = output_dir_for(cfg, source, fp)
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    timings: dict[str, float] = {}
    warnings: list[str] = []
    report: dict = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "processing",
        "profile": profile_summary(cfg),
        "source": {"path": str(source), "name": source.name, "fingerprint": fp, "size_bytes": before[0]},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "versions": _versions(),
        "warnings": warnings,
    }

    def mark(stage: str, start: float) -> None:
        timings[stage] = round(time.time() - start, 2)

    try:
        say("medya bilgisi", 0.02)
        t = time.time()
        info = probe(source)
        report["media"] = info.to_dict()
        mark("probe", t)

        say("sessizlik analizi", 0.08)
        t = time.time()
        silences: list[dict] = []
        if info.has_audio:
            silences = detect_silences(source, info.duration_sec, cfg.analysis.silence_noise_db, cfg.analysis.silence_min_sec)
        else:
            warnings.append("Videoda ses akışı yok: transkript ve sessizlik analizi yapılmadı, taslaklar sessiz olacak.")
        report["silences"] = silences
        mark("silences", t)

        features = report["features"] = {}
        say("sahne geçişleri", 0.15)
        t = time.time()
        cuts: list[float] = []
        if not cfg.analysis.detect_scenes:
            features["detect_scenes"] = _st("disabled", "Yapılandırmada kapalı.")
        else:
            try:
                cuts = detect_scene_cuts(source, cfg.analysis.scene_threshold)
                features["detect_scenes"] = _st("applied", f"PySceneDetect ContentDetector: {len(cuts)} geçiş bulundu.")
            except Exception as exc:  # sahne analizi zorunlu değil
                warnings.append(f"Sahne analizi yapılamadı: {exc}")
                features["detect_scenes"] = _st("failed", f"Sahne analizi yapılamadı: {exc}")
        report["scene_cuts"] = cuts
        mark("scenes", t)

        transcript: Transcript | None = None
        if info.has_audio and cfg.transcribe.enabled:
            say("transkripsiyon", 0.25)
            t = time.time()
            engine = transcriber or FasterWhisperTranscriber(cfg.transcribe)
            with tempfile.TemporaryDirectory(prefix="vea_") as tmp:
                wav = extract_audio_wav(source, Path(tmp) / "audio.wav")
                transcript = engine.transcribe(wav, info.duration_sec, lambda p: say("transkripsiyon", 0.25 + 0.4 * p))
            _write_json(out_dir / "transcript.json", transcript.to_dict())
            mark("transcribe", t)
            if not transcript.segments:
                warnings.append("Ses var ancak konuşma algılanmadı; adaylar sahne geçişlerine göre seçildi.")
            if cfg.transcribe.language and transcript.language != cfg.transcribe.language:
                warnings.append(f"Algılanan dil '{transcript.language}', beklenen '{cfg.transcribe.language}'.")
        elif info.has_audio:
            warnings.append("Transkripsiyon yapılandırmada kapalı (transcribe.enabled = false).")

        cues = segments_to_cues(transcript.segments) if transcript else []
        if cues:
            (out_dir / "full.srt").write_text(to_srt(cues), encoding="utf-8")
        report["transcript"] = None if transcript is None else {
            "file": "transcript.json",
            "srt": "full.srt" if cues else None,
            "engine": transcript.engine,
            "model": transcript.model,
            "language": transcript.language,
            "language_probability": transcript.language_probability,
            "segment_count": len(transcript.segments),
            "text": transcript.text,
        }

        say("kesit adayları", 0.68)
        t = time.time()
        if transcript and transcript.segments:
            picks = cand.from_transcript(transcript.segments, info.duration_sec, silences, cuts, cfg.candidates, cfg.edit)
        else:
            picks = cand.from_scenes(info.duration_sec, cuts, cfg.candidates)
        cand_dicts = [c.to_dict() for c in picks]
        mark("candidates", t)
        features["smart_cuts"] = (
            _st("applied", "Kesit sınırları cümle sınırlarına (nefes payıyla) ve yakın sahne geçişlerine hizalandı.")
            if cfg.edit.smart_cuts and transcript and transcript.segments else
            _st("disabled", "Yapılandırmada kapalı; tam segment sınırları kullanıldı.") if not cfg.edit.smart_cuts else
            _st("not_applicable", "Transkript yok; kesitler sahne sınırlarına göre seçildi."))
        features["duration_mode"] = _st(
            cfg.candidates.duration_mode,
            f"Hedef aralık {cfg.candidates.min_sec:.0f}–{cfg.candidates.max_sec:.0f} sn (kurgu sonrası süre)"
            if cfg.candidates.duration_mode == "auto" else f"Hedef {cfg.candidates.target_sec:.0f} sn")

        ok, reason = semantic_scoring.availability(cfg.cloud)
        cloud_info = {"used": False, "reason": reason, "sent": "nothing"}
        # Test için sahte istemci verilebilir; yine de yapılandırma + onay şartı aranır.
        if ok or (cloud_client is not None and cfg.cloud.enabled and cfg.cloud.consent_send_transcript):
            say("bulut puanlama", 0.7)
            try:
                scores = semantic_scoring.score_candidates(cand_dicts, cfg.cloud, client=cloud_client)
                for c in cand_dicts:
                    if c["id"] in scores:
                        c["cloud_score"] = {**scores[c["id"]], "source": "model_output", "model": cfg.cloud.model,
                                            "note": "Bulut modelinin çıktısıdır; transkripte göre doğrulanmamıştır."}
                cloud_info = {"used": True, "reason": reason, "sent": "transcript_text_only", "model": cfg.cloud.model}
            except Exception as exc:
                cloud_info = {"used": False, "reason": f"Bulut puanlama başarısız, yerel puanlar kullanıldı: {exc}", "sent": "unknown"}
                warnings.append(cloud_info["reason"])
        report["cloud"] = cloud_info
        report["candidates"] = cand_dicts

        report["framing"] = {
            "mode": "center_crop_fixed",
            "face_tracking": False,
            "note": "Kadraj sabit merkez kırpma ile oluşturuldu; yüz takibi yapılmadı. Konuşmacı merkez dışındaysa kadraj dışında kalabilir.",
        }
        report["audio"] = {"level_modified": False, "music_added": False, "sound_effects_added": False,
                           "note": "Orijinal ses seviyesi korunur; yalnızca AAC'ye yeniden kodlanır." if info.has_audio else "Kaynakta ses yok."}

        cap = cfg.captions
        segments = transcript.segments if transcript else []
        renders = []
        if cfg.render.enabled:
            for idx, c in enumerate(cand_dicts):
                base = 0.72 + 0.27 * idx / max(1, len(cand_dicts))
                say(f"render {c['id']}", base)
                entry: dict = {"candidate_id": c["id"], "status": "failed"}
                t = time.time()
                try:
                    plan = plan_clip(c["start"], c["end"], silences, segments, cfg.edit, cfg.zoom_factor, info.has_audio)
                    clip_segs = map_segments(segments, plan) if segments else []
                    cfeat = dict(plan.features)
                    entry["edit"] = {"pieces": [p.to_dict() for p in plan.pieces], "removed_silences": plan.removed_silences,
                                     "output_duration_planned": round(plan.output_duration, 3)}
                    ass_file = None
                    if not cap.enabled:
                        cfeat["captions"] = _st("disabled", "Yapılandırmada kapalı.")
                    elif not clip_segs:
                        cfeat["captions"] = _st("not_applicable", "Bu kesitte konuşma yok; altyazı üretilmedi.")
                    else:
                        if cap.export_srt:
                            srt_file = clips_dir / f"{c['id']}.srt"
                            srt_file.write_text(to_srt(segments_to_cues(clip_segs)), encoding="utf-8")
                            entry["srt"] = f"clips/{srt_file.name}"
                        events, timing = build_events(clip_segs, cap)
                        ass_file = clips_dir / f"{c['id']}.ass"
                        ass_file.write_text(to_ass(events, cap, cfg.render.width, cfg.render.height), encoding="utf-8")
                        entry["ass"] = f"clips/{ass_file.name}"
                        style = cap.style if timing == "word_level" else "plain"
                        detail = (f"Zamanlama: {timing}; stil: {style}"
                                  + ("; aktif kelime vurgulu" if style == "dynamic_highlight" and cap.highlight_active_word else "")
                                  + ("; videoya işlendi (ASS/libass)" if cap.burn else "; videoya işlenmedi (yalnızca dosya)"))
                        if cap.timing == "word_level" and timing != "word_level":
                            detail += ". Kelime zamanı bulunamadı; segment zamanlamasına düşüldü."
                        cfeat["captions"] = _st("applied", detail)
                    entry["features"] = cfeat
                    mp4 = render_clip(source, c["start"], c["end"], clips_dir / f"{c['id']}.mp4", cfg.render,
                                      info.has_audio, ass_file if cap.burn else None,
                                      lambda p, b=base: say(f"render {c['id']}", b + 0.27 / max(1, len(cand_dicts)) * p),
                                      pieces=[(p.src_start, p.src_end, p.zoom) for p in plan.pieces])
                    out_info = probe(mp4)
                    entry.update({
                        "status": "ok", "file": f"clips/{mp4.name}", "width": out_info.width, "height": out_info.height,
                        "aspect_ratio": aspect_ratio(out_info.width, out_info.height),
                        "duration_sec": out_info.duration_sec, "fps": out_info.fps, "has_audio": out_info.has_audio,
                        "subtitles_burned": bool(cap.burn and ass_file),
                    })
                    lo, hi = cfg.candidates.min_sec, cfg.candidates.max_sec
                    if cfg.candidates.duration_mode == "auto" and not lo - 0.5 <= out_info.duration_sec <= hi + 0.5:
                        entry["duration_note"] = c.get("duration_note") or (
                            f"Çıktı süresi {out_info.duration_sec:.1f} sn, {lo:.0f}–{hi:.0f} sn hedefinin dışında")
                except (RenderError, MediaError, OSError) as exc:
                    entry["error"] = str(exc)
                    warnings.append(f"{c['id']} render edilemedi: {exc}")
                    log.error("%s render edilemedi: %s", c["id"], exc)
                entry["render_sec"] = round(time.time() - t, 2)
                renders.append(entry)
        report["renders"] = renders
        report["review"] = {
            "required": cfg.delivery.require_review_before_delivery,
            "status": "pending" if cfg.delivery.require_review_before_delivery else "not_required",
            "note": ("Taslaklar teslim edilmeden önce incelenmeli: python -m umbrella_vea review approve <iş_no>"
                     if cfg.delivery.require_review_before_delivery else "İnceleme zorunlu değil."),
        }
        report["publishing"] = {"auto_publish": False, "note": "Otomatik yayın özelliği yoktur; hiçbir platforma yükleme yapılmaz."}

        if _snapshot(source) != before:
            raise RuntimeError("Orijinal dosya işlem sırasında değişti! (boyut veya değiştirilme zamanı farklı)")
        report["source"]["unchanged"] = True

        rendered = sum(1 for r in renders if r["status"] == "ok")
        if cfg.render.enabled and cand_dicts and rendered == 0:
            raise RenderError("Hiçbir taslak render edilemedi")
        report["status"] = "done" if rendered == len(renders) else "done_with_errors"
        if report["status"] == "done_with_errors":
            raise PartialRenderError(f"{len(renders) - rendered}/{len(renders)} kesit render edilemedi; 'retry' ile yeniden denenebilir")
        if not cfg.delivery.require_review_before_delivery:
            from .review import deliver

            report["review"]["delivered"] = deliver(out_dir, report, [r["candidate_id"] for r in renders], cfg)
        say("tamamlandı", 1.0)
        return out_dir, report
    except Exception as exc:
        if not isinstance(exc, PartialRenderError):
            report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        timings["total"] = round(time.time() - t0, 2)
        report["timings_sec"] = timings
        _write_json(out_dir / "report.json", report)
