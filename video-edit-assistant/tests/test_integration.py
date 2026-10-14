"""FFmpeg ile gerçek dosyalar üzerinde entegrasyon testleri: render, izleyici, bulut kapısı."""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from umbrella_vea.cloud import semantic_scoring
from umbrella_vea.config import RenderConfig
from umbrella_vea.ffmpeg_tools import probe
from umbrella_vea.pipeline import process_video
from umbrella_vea.render import RenderError, render_clip
from umbrella_vea.state import DONE, FAILED, JobStore, fingerprint
from umbrella_vea.watcher import Watcher, validate_dirs

from conftest import make_video
from doubles import FakeCloudClient


# --- render -----------------------------------------------------------------

@pytest.mark.parametrize("size,audio", [("640x360", True), ("360x640", True), ("480x480", False)])
def test_render_produces_1080x1920(tmp_path, size, audio):
    src = make_video(tmp_path / "src.mp4", 3, size, audio)
    out = render_clip(src, 0.5, 2.5, tmp_path / "clip.mp4", RenderConfig(preset="ultrafast"), audio)
    info = probe(out)
    assert (info.width, info.height) == (1080, 1920)
    assert abs(info.duration_sec - 2.0) < 0.2
    assert info.has_audio is audio
    assert not (tmp_path / "clip.part.mp4").exists()


def test_render_failure_raises_and_leaves_no_partial_file(tmp_path):
    bad = tmp_path / "yok.mp4"
    bad.write_bytes(b"0" * 100)
    with pytest.raises(RenderError):
        render_clip(bad, 0, 1, tmp_path / "clip.mp4", RenderConfig(preset="ultrafast"), True)
    assert not list(tmp_path.glob("clip*"))


# --- izleyici -----------------------------------------------------------------

def _recording_processor(calls: list):
    def proc(path: Path, fp: str) -> Path:
        calls.append(path.name)
        return path.parent
    return proc


def test_output_inside_input_is_rejected(cfg):
    cfg.paths.output_dir = cfg.paths.input_dir / "out"
    with pytest.raises(ValueError):
        validate_dirs(cfg)


def test_watcher_waits_until_copy_finishes(cfg, tmp_path):
    src = make_video(tmp_path / "kaynak.mp4", 2)
    data = src.read_bytes()
    dest = cfg.input_dir / "yeni.mp4"
    calls: list = []
    store = JobStore(cfg.state_dir / "j.db")
    w = Watcher(cfg, store, _recording_processor(calls))
    with dest.open("wb") as f:  # kopyalama sürüyor: dosya büyüyor
        for i in range(0, len(data), max(1, len(data) // 5)):
            f.write(data[i : i + max(1, len(data) // 5)])
            f.flush()
            w.poll_once()
            assert calls == [], "kopyalama bitmeden işleme başlanmamalı"
    for _ in range(cfg.watch.stable_checks + 1):
        w.poll_once()
    assert calls == ["yeni.mp4"]
    store.close()


def test_watcher_ignores_temp_hidden_and_unsupported_files(cfg):
    for name in (".gizli.mp4", "indiriliyor.mp4.part", "notlar.txt", "video.mp4.crdownload"):
        (cfg.input_dir / name).write_bytes(b"x" * 100)
    calls: list = []
    store = JobStore(cfg.state_dir / "j.db")
    w = Watcher(cfg, store, _recording_processor(calls))
    for _ in range(4):
        w.poll_once()
    assert calls == [] and store.all() == []
    store.close()


def test_same_video_is_processed_once_even_if_renamed(cfg, tmp_path):
    src = make_video(tmp_path / "kaynak.mp4", 2)
    shutil.copy(src, cfg.input_dir / "a.mp4")
    calls: list = []
    store = JobStore(cfg.state_dir / "j.db")
    w = Watcher(cfg, store, _recording_processor(calls))
    for _ in range(4):
        w.poll_once()
    shutil.copy(src, cfg.input_dir / "a_kopya.mp4")
    for _ in range(4):
        w.poll_once()
    assert calls == ["a.mp4"]
    assert len(store.all()) == 1 and store.all()[0].status == DONE
    store.close()


def test_corrupt_file_is_logged_as_failed(cfg, caplog):
    (cfg.input_dir / "bozuk.mp4").write_bytes(b"bozuk" * 1000)
    calls: list = []
    store = JobStore(cfg.state_dir / "j.db")
    w = Watcher(cfg, store, _recording_processor(calls))
    with caplog.at_level("ERROR", logger="umbrella_vea"):
        for _ in range(4):
            w.poll_once()
    job = store.all()[0]
    assert calls == [] and job.status == FAILED and "bozuk" in job.error.lower()
    assert any("başarısız" in r.message for r in caplog.records)
    store.close()


def test_failed_job_retries_up_to_limit_then_manual_retry(cfg, tmp_path):
    shutil.copy(make_video(tmp_path / "k.mp4", 2), cfg.input_dir / "v.mp4")
    attempts = {"n": 0}

    def flaky(path: Path, fp: str) -> Path:
        attempts["n"] += 1
        if attempts["n"] < 4:
            raise RuntimeError("geçici hata")
        return path.parent

    store = JobStore(cfg.state_dir / "j.db")
    w = Watcher(cfg, store, flaky)
    for _ in range(8):
        w.poll_once()
    job = store.all()[0]
    assert job.status == FAILED and attempts["n"] == cfg.watch.max_attempts
    for _ in range(4):  # limit doldu: otomatik deneme yok
        w.poll_once()
    assert attempts["n"] == cfg.watch.max_attempts
    assert store.reset_for_retry(job.id)  # 'retry' komutu: deneme hakkı sıfırlanır
    for _ in range(4):
        w.poll_once()
    assert attempts["n"] == 4 and store.get(job.id).status == DONE
    store.close()


def test_empty_input_folder_is_handled(cfg, caplog):
    store = JobStore(cfg.state_dir / "j.db")
    w = Watcher(cfg, store, _recording_processor([]))
    with caplog.at_level("INFO", logger="umbrella_vea"):
        w.run(once=True)
    assert any("boş" in r.message for r in caplog.records)
    store.close()


# --- bulut kapısı ----------------------------------------------------------------

def test_cloud_disabled_by_default_and_requires_consent_and_key(cfg, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert semantic_scoring.availability(cfg.cloud)[0] is False
    cfg.cloud.enabled = True
    ok, reason = semantic_scoring.availability(cfg.cloud)
    assert not ok and "onay" in reason
    cfg.cloud.consent_send_transcript = True
    ok, reason = semantic_scoring.availability(cfg.cloud)
    assert not ok and "ANTHROPIC_API_KEY" in reason
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy-key-000000")
    assert semantic_scoring.availability(cfg.cloud)[0] is True
    assert "sk-ant" not in semantic_scoring.availability(cfg.cloud)[1]


def test_cloud_sends_only_transcript_text(cfg):
    cands = [{"id": "clip_01", "start": 1.0, "end": 9.0, "text": "Peki neden önemli?", "reasons": []}]
    client = FakeCloudClient(json.dumps({"scores": [{"id": "clip_01", "score": 0.8, "reason": "Net bir soru."},
                                                    {"id": "uydurma", "score": 1, "reason": "x"}]}))
    out = semantic_scoring.score_candidates(cands, cfg.cloud, client=client)
    assert out == {"clip_01": {"score": 0.8, "reason": "Net bir soru."}}
    sent = client.sent[0]
    body = json.loads(sent["messages"][0]["content"])
    assert body == {"candidates": [{"id": "clip_01", "start": 1.0, "end": 9.0, "text": "Peki neden önemli?"}]}
    assert all(isinstance(m["content"], str) for m in sent["messages"])  # medya bloğu yok


def test_cloud_refusal_raises(cfg):
    client = FakeCloudClient("{}", stop_reason="refusal")
    with pytest.raises(RuntimeError, match="reddetti"):
        semantic_scoring.score_candidates([{"id": "c", "start": 0, "end": 1, "text": "a"}], cfg.cloud, client=client)


# --- ses akışı olmayan video -----------------------------------------------------

def test_video_without_audio_still_produces_draft(cfg, tmp_path):
    src = shutil.copy(make_video(tmp_path / "k.mp4", 4, audio=False), cfg.input_dir / "sessiz.mp4")
    src = Path(src)
    out_dir, report = process_video(src, cfg, fingerprint(src))
    assert report["status"] == "done"
    assert report["transcript"] is None
    assert any("ses akışı yok" in w for w in report["warnings"])
    assert report["candidates"][0]["basis"] in ("scenes", "fallback")
    r = report["renders"][0]
    assert r["status"] == "ok" and (r["width"], r["height"]) == (1080, 1920) and r["has_audio"] is False


# --- inceleme sonrası regresyon testleri -------------------------------------

@pytest.mark.parametrize("field", ["state_dir", "log_dir"])
def test_state_or_logs_inside_input_are_rejected(cfg, field):
    setattr(cfg.paths, field, cfg.paths.input_dir / "x")
    with pytest.raises(ValueError):
        validate_dirs(cfg)


def test_output_symlink_into_input_is_rejected(cfg, tmp_path):
    link = tmp_path / "link_out"
    link.symlink_to(cfg.input_dir, target_is_directory=True)
    cfg.paths.output_dir = link
    with pytest.raises(ValueError):
        validate_dirs(cfg)


def test_partial_render_failure_is_retryable(cfg, tmp_path, monkeypatch):
    import umbrella_vea.pipeline as pl

    cfg.transcribe.enabled = False  # bu test render hatasıyla ilgili
    src = Path(shutil.copy(make_video(tmp_path / "k.mp4", 4), cfg.input_dir / "v.mp4"))
    real = pl.render_clip

    def broken(*a, **k):
        raise RenderError("disk dolu")

    monkeypatch.setattr(pl, "render_clip", broken)
    store = JobStore(cfg.state_dir / "j.db")
    w = Watcher(cfg, store, lambda p, fp: pl.process_video(p, cfg, fp)[0])
    for _ in range(4):
        w.poll_once()
    job = store.all()[0]
    assert job.status == FAILED and "render edilemedi" in job.error
    monkeypatch.setattr(pl, "render_clip", real)
    assert store.reset_for_retry(job.id)
    w.poll_once()
    assert store.get(job.id).status == DONE
    store.close()


def test_retry_runs_job_registered_outside_input(cfg, tmp_path):
    outside = make_video(tmp_path / "disarida.mp4", 2)
    store = JobStore(cfg.state_dir / "j.db")
    job, _ = store.register(fingerprint(outside), outside)
    store.start(job.id)
    store.fail(job.id, "ilk deneme başarısız")
    cfg.watch.max_attempts = 1
    calls: list = []
    w = Watcher(cfg, store, _recording_processor(calls))
    w.poll_once()
    assert calls == []  # deneme hakkı bitti
    store.reset_for_retry(job.id)
    w.poll_once()
    assert calls == ["disarida.mp4"] and store.get(job.id).status == DONE
    store.close()


def test_cloud_reason_is_labelled_as_model_output(cfg, tmp_path):
    src = Path(shutil.copy(make_video(tmp_path / "k.mp4", 3), cfg.input_dir / "v.mp4"))
    cfg.cloud.enabled = cfg.cloud.consent_send_transcript = True

    class T:
        def transcribe(self, audio, duration, progress=None):
            from umbrella_vea.transcribe import Segment, Transcript
            return Transcript("tr", 1.0, duration, "test-double", "x", [Segment(0.2, 2.5, "Bu bir deneme cümlesi.", -0.2, 0.01)])

    client = FakeCloudClient(json.dumps({"scores": [{"id": "clip_01", "score": 0.5, "reason": "Model yorumu."}]}))
    _, report = process_video(src, cfg, fingerprint(src), transcriber=T(), cloud_client=client)
    cs = report["candidates"][0]["cloud_score"]
    assert cs["source"] == "model_output" and "doğrulanmamıştır" in cs["note"]
    assert report["cloud"]["used"] is True and report["cloud"]["sent"] == "transcript_text_only"
