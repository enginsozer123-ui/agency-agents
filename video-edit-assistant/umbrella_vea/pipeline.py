"""Tek bir videoyu uçtan uca işler: analiz → transkript → adaylar → 9:16 taslaklar → JSON rapor."""

from __future__ import annotations

import json
import logging
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
from .render import RenderError, render_clip
from .scenes import detect_scene_cuts
from .srt import clip_cues, segments_to_cues, to_srt
from .transcribe import FasterWhisperTranscriber, Transcriber, Transcript

log = logging.getLogger("umbrella_vea.pipeline")

REPORT_SCHEMA_VERSION = 1


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

        say("sahne geçişleri", 0.15)
        t = time.time()
        try:
            cuts = detect_scene_cuts(source, cfg.analysis.scene_threshold)
        except Exception as exc:  # sahne analizi zorunlu değil
            cuts = []
            warnings.append(f"Sahne analizi yapılamadı: {exc}")
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
            picks = cand.from_transcript(transcript.segments, info.duration_sec, silences, cuts, cfg.candidates)
        else:
            picks = cand.from_scenes(info.duration_sec, cuts, cfg.candidates)
        cand_dicts = [c.to_dict() for c in picks]
        mark("candidates", t)

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
        report["audio"] = {"level_modified": False, "music_added": False,
                           "note": "Orijinal ses seviyesi korunur; yalnızca AAC'ye yeniden kodlanır." if info.has_audio else "Kaynakta ses yok."}

        renders = []
        if cfg.render.enabled:
            for idx, c in enumerate(cand_dicts):
                base = 0.72 + 0.27 * idx / max(1, len(cand_dicts))
                say(f"render {c['id']}", base)
                entry: dict = {"candidate_id": c["id"], "status": "failed"}
                t = time.time()
                try:
                    ccues = clip_cues(cues, c["start"], c["end"])
                    srt_file = None
                    if ccues and (cfg.render.write_srt or cfg.render.burn_subtitles):
                        srt_file = clips_dir / f"{c['id']}.srt"
                        srt_file.write_text(to_srt(ccues), encoding="utf-8")
                        if cfg.render.write_srt:
                            entry["srt"] = f"clips/{srt_file.name}"
                    mp4 = render_clip(source, c["start"], c["end"], clips_dir / f"{c['id']}.mp4", cfg.render,
                                      info.has_audio, srt_file if cfg.render.burn_subtitles else None,
                                      lambda p, b=base: say(f"render {c['id']}", b + 0.27 / max(1, len(cand_dicts)) * p))
                    if srt_file and not cfg.render.write_srt:
                        srt_file.unlink(missing_ok=True)
                    out_info = probe(mp4)
                    entry.update({
                        "status": "ok", "file": f"clips/{mp4.name}", "width": out_info.width, "height": out_info.height,
                        "duration_sec": out_info.duration_sec, "fps": out_info.fps, "has_audio": out_info.has_audio,
                        "subtitles_burned": bool(cfg.render.burn_subtitles and ccues),
                    })
                    if not ccues:
                        entry["note"] = "Bu kesitte altyazı yok (konuşma algılanmadı)."
                except (RenderError, MediaError, OSError) as exc:
                    entry["error"] = str(exc)
                    warnings.append(f"{c['id']} render edilemedi: {exc}")
                    log.error("%s render edilemedi: %s", c["id"], exc)
                entry["render_sec"] = round(time.time() - t, 2)
                renders.append(entry)
        report["renders"] = renders

        if _snapshot(source) != before:
            raise RuntimeError("Orijinal dosya işlem sırasında değişti! (boyut veya değiştirilme zamanı farklı)")
        report["source"]["unchanged"] = True

        rendered = sum(1 for r in renders if r["status"] == "ok")
        if cfg.render.enabled and cand_dicts and rendered == 0:
            raise RenderError("Hiçbir taslak render edilemedi")
        report["status"] = "done" if rendered == len(renders) else "done_with_errors"
        if report["status"] == "done_with_errors":
            raise PartialRenderError(f"{len(renders) - rendered}/{len(renders)} kesit render edilemedi; 'retry' ile yeniden denenebilir")
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
