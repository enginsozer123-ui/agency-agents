"""Uçtan uca test: Türkçe konuşmalı örnek video input/ klasörüne kopyalanır, izleyici algılar ve işler.

İki sürüm vardır:
- test_e2e_watch_folder: ASR yerine test dublörü (ScriptedTranscriber) kullanır. Konuşma zamanları
  ses dosyasından gerçekten ölçülür, metin bilinen senaryodan gelir. Diğer tüm adımlar gerçektir.
- test_e2e_real_whisper: gerçek faster-whisper modelini kullanır. Model indirilebilen ortamda
  VEA_REAL_WHISPER=1 ile çalıştırılır; aksi halde atlanır (SKIPPED olarak raporlanır).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from umbrella_vea.ffmpeg_tools import probe
from umbrella_vea.pipeline import process_video
from umbrella_vea.state import DONE, JobStore
from umbrella_vea.transcribe import FasterWhisperTranscriber
from umbrella_vea.watcher import Watcher

from doubles import ScriptedTranscriber
from make_sample import SENTENCES, has_tts, make_sample

pytestmark = pytest.mark.skipif(not has_tts(), reason="espeak-ng kurulu değil (Türkçe örnek konuşma üretilemiyor)")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _run_watch(cfg, transcriber, tmp_path):
    sample = make_sample(tmp_path / "ornek_tr.mp4")
    dest = cfg.input_dir / "Musteri Videosu 1.mp4"
    shutil.copy(sample, dest)
    sha_before, stat_before = _sha(dest), dest.stat()

    store = JobStore(cfg.state_dir / "jobs.sqlite3")
    reports: list = []

    def processor(path: Path, fp: str) -> Path:
        out_dir, report = process_video(path, cfg, fp, transcriber=transcriber)
        reports.append(report)
        return out_dir

    Watcher(cfg, store, processor).run(once=True, max_wait_sec=60)
    jobs = store.all()
    store.close()
    return dest, sha_before, stat_before, jobs, reports


def _assert_acceptance(cfg, dest, sha_before, stat_before, jobs, reports):
    # 1. algılandı ve bir kez işlendi
    assert len(jobs) == 1 and jobs[0].status == DONE, jobs
    report = reports[0]
    out_dir = Path(jobs[0].output_dir)
    saved = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert saved["status"] == "done"

    # 2. video bilgileri ve Türkçe transkript
    media = saved["media"]
    assert media["width"] == 1280 and media["height"] == 720 and media["fps"] == 25.0 and media["has_audio"]
    assert media["duration_sec"] > 10
    t = saved["transcript"]
    assert t["language"] == "tr" and t["segment_count"] >= 1 and t["text"]
    assert (out_dir / "transcript.json").exists() and (out_dir / "full.srt").exists()
    assert saved["scene_cuts"], "örnek videoda bir sahne geçişi var"
    assert saved["silences"], "örnek videoda cümle arası sessizlikler var"

    # 3. en az bir anlamlı aday: zaman kodu, süre, gerekçe, güven
    c = saved["candidates"][0]
    assert 0 <= c["start"] < c["end"] <= media["duration_sec"] + 0.01
    assert abs(c["duration"] - (c["end"] - c["start"])) < 0.01
    assert c["reasons"] and 0 < c["confidence"] <= 1 and c["basis"] == "transcript"

    # 4. geçerli 9:16 MP4 + SRT
    r = saved["renders"][0]
    assert r["status"] == "ok" and r["subtitles_burned"]
    mp4 = out_dir / r["file"]
    info = probe(mp4)
    assert (info.width, info.height) == (1080, 1920) and info.has_audio
    assert abs(info.duration_sec - r["edit"]["output_duration_planned"]) < 0.3
    srt = (out_dir / r["srt"]).read_text(encoding="utf-8")
    assert "-->" in srt and srt.startswith("1\n")
    assert saved["framing"]["face_tracking"] is False
    assert saved["audio"]["level_modified"] is False

    # 5. orijinal dosya korunuyor (içerik, boyut, zaman damgası; hâlâ yerinde)
    assert dest.exists() and _sha(dest) == sha_before
    assert dest.stat().st_size == stat_before.st_size and dest.stat().st_mtime_ns == stat_before.st_mtime_ns
    assert saved["source"]["unchanged"] is True
    assert sorted(p.name for p in cfg.input_dir.iterdir()) == [dest.name]  # input/ klasörüne hiçbir şey yazılmadı

    # 7. bulut olmadan yerel mod
    assert saved["cloud"]["used"] is False and saved["cloud"]["sent"] == "nothing"

    # viral-reels profili gerçekten uygulandı
    assert saved["profile"]["id"] == "viral-reels"
    assert r["aspect_ratio"] == "9:16"
    f = r["features"]
    assert f["remove_silence"]["status"] == "applied" and r["edit"]["removed_silences"]
    assert f["punch_in_zooms"]["status"] == "applied" and any(p["zoom"] > 1 for p in r["edit"]["pieces"])
    assert "word_level" in f["captions"]["detail"] and "dynamic_highlight" in f["captions"]["detail"]
    assert saved["features"]["detect_scenes"]["status"] == "applied"
    assert 15 <= info.duration_sec <= 60  # otomatik süre: hedef aralıkta
    assert (out_dir / r["ass"]).exists()
    assert saved["review"]["status"] == "pending" and not (out_dir / "delivery").exists()
    return saved


def test_e2e_watch_folder(cfg, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = _run_watch(cfg, ScriptedTranscriber(SENTENCES), tmp_path)
    saved = _assert_acceptance(cfg, *result)
    assert saved["transcript"]["engine"] == "test-double"
    best = max(saved["candidates"], key=lambda c: c["confidence"])
    assert any("Soru-cevap" in r for r in best["reasons"])

    # teslim öncesi inceleme: CLI ile onay → delivery/ klasörü
    from umbrella_vea.__main__ import main

    cfg_file = tmp_path / "cli.toml"
    cfg_file.write_text(
        f'[profile]\nid = "viral-reels"\n[paths]\ninput_dir = "{cfg.input_dir}"\noutput_dir = "{cfg.output_dir}"\n'
        f'state_dir = "{cfg.state_dir}"\nlog_dir = "{cfg.log_dir}"\n', encoding="utf-8")
    assert main(["--config", str(cfg_file), "review", "approve", "1", "--note", "uygun"]) == 0
    out_dir = cfg.output_dir / next(p.name for p in cfg.output_dir.iterdir())
    assert (out_dir / "delivery" / "clip_01.mp4").exists()
    import logging
    logging.getLogger("umbrella_vea").handlers.clear()

    # aynı video ikinci kez bırakılırsa (farklı adla) yeniden işlenmez
    shutil.copy(result[0], cfg.input_dir / "ayni_video_kopya.mp4")
    store = JobStore(cfg.state_dir / "jobs.sqlite3")
    calls: list = []
    Watcher(cfg, store, lambda p, fp: calls.append(p) or p.parent).run(once=True, max_wait_sec=30)
    assert calls == [] and len(store.all()) == 1
    store.close()


@pytest.mark.skipif(os.environ.get("VEA_REAL_WHISPER") != "1",
                    reason="Gerçek Whisper testi için VEA_REAL_WHISPER=1 (ilk çalıştırmada model internetten indirilir)")
def test_e2e_real_whisper(cfg, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg.transcribe.model = os.environ.get("VEA_WHISPER_MODEL", "small")
    saved = _assert_acceptance(cfg, *_run_watch(cfg, FasterWhisperTranscriber(cfg.transcribe), tmp_path))
    assert saved["transcript"]["engine"] == "faster-whisper"
