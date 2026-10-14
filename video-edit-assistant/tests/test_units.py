"""Hızlı birim testleri (ağ ve model gerektirmez)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from umbrella_vea import candidates as cand
from umbrella_vea.config import CandidateConfig, load_config
from umbrella_vea.ffmpeg_tools import MediaError, parse_silencedetect, probe
from umbrella_vea.logging_setup import redact
from umbrella_vea.render import crop_filter
from umbrella_vea.srt import Cue, clip_cues, fmt_ts, segments_to_cues, to_srt
from umbrella_vea.state import DONE, FAILED, PENDING, PROCESSING, JobStore, fingerprint
from umbrella_vea.transcribe import Segment, Transcript, Word

from conftest import ROOT


# --- config ---------------------------------------------------------------

def test_example_config_loads_and_resolves_relative_paths(tmp_path):
    cfg = load_config(ROOT / "config.example.toml", base_dir=tmp_path)
    assert cfg.input_dir == tmp_path / "input"
    assert cfg.cloud.enabled is False and cfg.cloud.consent_send_transcript is False
    assert cfg.render.width == 1080 and cfg.render.height == 1920


def test_unknown_config_key_is_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[render]\nwidht = 1080\n")
    with pytest.raises(ValueError, match="widht"):
        load_config(p)


# --- SRT ------------------------------------------------------------------

def test_srt_timestamp_format():
    assert fmt_ts(0) == "00:00:00,000"
    assert fmt_ts(3725.5) == "01:02:05,500"


def test_srt_output_and_wrapping():
    out = to_srt([Cue(1.0, 2.5, "Merhaba dünya"), Cue(3.0, 4.0, "a " * 30)])
    blocks = out.strip().split("\n\n")
    assert blocks[0] == "1\n00:00:01,000 --> 00:00:02,500\nMerhaba dünya"
    assert len(blocks[1].splitlines()) <= 4  # numara + zaman + en fazla 2 satır


def test_long_segment_split_by_words():
    words = [Word(i * 0.5, i * 0.5 + 0.4, f"kelime{i}", 0.9) for i in range(30)]
    seg = Segment(0.0, 15.0, " ".join(w.text for w in words), words=words)
    cues = segments_to_cues([seg])
    assert len(cues) > 1
    assert all(c.end - c.start <= 6.0 for c in cues)
    assert " ".join(c.text for c in cues) == seg.text  # kelime kaybı yok


def test_clip_cues_shift_to_clip_start():
    cues = [Cue(0, 2, "a"), Cue(5, 7, "b"), Cue(9, 12, "c")]
    out = clip_cues(cues, 6.0, 10.0)
    assert [(c.start, c.end, c.text) for c in out] == [(0.0, 1.0, "b"), (3.0, 4.0, "c")]


# --- kesit adayları --------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Peki neden bu kadar önemli?", True),
    ("Bunu biliyor musunuz", True),
    ("Nasıl başlamalıyız", True),
    ("Bugün hava çok güzel.", False),
])
def test_turkish_question_detection(text, expected):
    assert cand.is_question(text) is expected


def test_strong_phrase_detection_is_turkish_case_aware():
    assert "en önemli" in cand.strong_hits("İşte EN ÖNEMLİ nokta")
    assert cand.strong_hits("Sıradan bir cümle.") == []


def _segs():
    return [
        Segment(0.5, 4.0, "Merhaba, bugün size en önemli hatayı anlatacağım.", -0.2, 0.01),
        Segment(5.5, 8.0, "Peki bu hata neden bu kadar yaygın?", -0.2, 0.01),
        Segment(9.5, 13.0, "Çünkü kimse ilk saniyelere dikkat etmiyor.", -0.2, 0.01),
        Segment(25.0, 29.0, "Başka bir konu.", -0.3, 0.02),
    ]


def test_candidates_from_transcript_have_times_reasons_and_confidence():
    cfg = CandidateConfig(min_sec=5, max_sec=20, target_sec=12, max_candidates=3)
    picks = cand.from_transcript(_segs(), 30.0, [{"start": 13.0, "end": 25.0, "duration": 12.0}], [], cfg)
    assert picks, "en az bir aday beklenir"
    best = max(picks, key=lambda c: c.confidence)
    assert best.start < 1.0 and 12.9 <= best.end <= 13.4
    assert abs(best.duration - (best.end - best.start)) < 1e-6
    assert 0 < best.confidence <= 1
    joined = " ".join(best.reasons)
    assert "Soru-cevap" in joined and "neden bu kadar yaygın?" in joined
    assert "en önemli" in joined
    # gerekçelerde alıntılanan her metin transkriptte gerçekten var
    for r in best.reasons:
        if "«" in r:
            quoted = r.split("«")[1].split("»")[0].rstrip("…")
            assert quoted in " ".join(s.text for s in _segs())


def test_candidates_do_not_overlap_beyond_limit():
    cfg = CandidateConfig(min_sec=3, max_sec=30, target_sec=10, max_candidates=5, max_overlap=0.3)
    picks = cand.from_transcript(_segs(), 30.0, [], [], cfg)
    for i, a in enumerate(picks):
        for b in picks[i + 1:]:
            assert cand._overlap(a, b) <= 0.3


def test_no_speech_falls_back_to_scenes_with_low_confidence():
    picks = cand.from_scenes(40.0, [10.0, 22.0], CandidateConfig())
    assert picks and all(c.basis == "scenes" for c in picks)
    assert all(c.confidence < 0.3 for c in picks)
    assert "Konuşma algılanmadı" in picks[0].reasons[0]
    assert cand.from_transcript([], 10.0, [], [], CandidateConfig()) == []


def test_transcript_roundtrip():
    t = Transcript("tr", 0.99, 10.0, "faster-whisper", "small", [Segment(0, 1, "a", words=[Word(0, 1, "a", 0.9)])])
    assert Transcript.from_dict(t.to_dict()) == t


# --- ffmpeg yardımcıları ----------------------------------------------------

def test_parse_silencedetect_including_trailing_silence():
    stderr = """
[silencedetect @ 0x1] silence_start: 0
[silencedetect @ 0x1] silence_end: 1.2 | silence_duration: 1.2
[silencedetect @ 0x1] silence_start: 8.5
"""
    assert parse_silencedetect(stderr, 10.0) == [
        {"start": 0.0, "end": 1.2, "duration": 1.2},
        {"start": 8.5, "end": 10.0, "duration": 1.5},
    ]


def test_probe_rejects_corrupt_file(tmp_path):
    bad = tmp_path / "bozuk.mp4"
    bad.write_bytes(b"bu bir video degil" * 100)
    with pytest.raises(MediaError):
        probe(bad)


def test_crop_filter_targets_9_16():
    f = crop_filter(1080, 1920)
    assert "crop=" in f and "scale=1080:1920" in f


# --- iş kayıtları ----------------------------------------------------------

def test_fingerprint_ignores_name_but_not_content(tmp_path):
    a = tmp_path / "a.mp4"; a.write_bytes(b"x" * 1000)
    b = tmp_path / "b.mp4"; b.write_bytes(b"x" * 1000)
    c = tmp_path / "c.mp4"; c.write_bytes(b"y" * 1000)
    assert fingerprint(a) == fingerprint(b) != fingerprint(c)


def test_jobstore_dedup_fail_retry_and_recovery(tmp_path):
    s = JobStore(tmp_path / "j.db")
    job, new = s.register("fp1", tmp_path / "a.mp4")
    assert new and job.status == PENDING
    again, new2 = s.register("fp1", tmp_path / "kopya.mp4")
    assert not new2 and again.id == job.id
    s.start(job.id)
    s.fail(job.id, "hata")
    assert s.get(job.id).status == FAILED and s.get(job.id).attempts == 1
    assert [j.id for j in s.runnable(max_attempts=2)] == [job.id]
    assert s.runnable(max_attempts=1) == []
    assert s.reset_for_retry(job.id) and s.get(job.id).status == PENDING
    s.start(job.id)
    assert s.get(job.id).status == PROCESSING
    assert s.recover_interrupted() == 1 and s.get(job.id).status == PENDING
    s.start(job.id); s.finish(job.id, tmp_path)
    assert s.get(job.id).status == DONE and not s.reset_for_retry(job.id)
    s.close()


# --- gizli anahtar maskeleme ------------------------------------------------

def test_api_key_is_redacted_from_logs(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-1234567890abcdef")
    assert "1234567890" not in redact("anahtar=sk-ant-test-1234567890abcdef")
    from umbrella_vea.logging_setup import setup_logging

    logger = setup_logging(tmp_path)
    logger.info("anahtar %s", "sk-ant-test-1234567890abcdef")
    for h in logger.handlers:
        h.flush()
    assert "1234567890abcdef" not in (tmp_path / "vea.log").read_text()
    logging.getLogger("umbrella_vea").handlers.clear()


# --- inceleme sonrası regresyon testleri -------------------------------------

def test_question_followed_by_question_is_not_qa():
    segs = [Segment(0, 3, "Neden?", -0.2, 0.01), Segment(4, 7, "Peki nasıl?", -0.2, 0.01),
            Segment(8, 11, "Tamam.", -0.2, 0.01)]
    w = cand._score_window(segs, 0, 1, 20.0, [], [], CandidateConfig(min_sec=1))
    assert w.features["qa"] == 0 and not any("Soru-cevap" in r for r in w.reasons)
    w2 = cand._score_window(segs, 1, 2, 20.0, [], [], CandidateConfig(min_sec=1))
    assert w2.features["qa"] == 1 and "«Tamam.»" in " ".join(w2.reasons)


def test_speech_running_to_video_end_is_not_called_clean_end():
    segs = [Segment(0.0, 9.9, "Konuşma videonun sonuna kadar sürüyor.", -0.2, 0.01)]
    w = cand._score_window(segs, 0, 0, 10.0, [], [], CandidateConfig(min_sec=1))
    assert not any("duraklamada bitiyor" in r for r in w.reasons)


def test_fallback_reason_matches_the_data():
    segs = [Segment(0, 3, "Kısa bir.", -0.2, 0.01), Segment(70, 73, "Başka kısa.", -0.2, 0.01)]
    picks = cand.from_transcript(segs, 80.0, [], [], CandidateConfig())
    assert picks and all(p.duration < CandidateConfig().min_sec for p in picks)
    for p in picks:
        assert "kısaltıldı" not in " ".join(p.reasons)
        assert "bütün bir konuşma bölümü yok" in p.duration_note and "toplam konuşma 6.0 sn" in p.duration_note


def test_traceback_is_redacted(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-abcdefghijkl")
    from umbrella_vea.logging_setup import setup_logging

    logger = setup_logging(tmp_path)
    try:
        raise RuntimeError("istek başarısız: anahtar sk-ant-secret-abcdefghijkl")
    except RuntimeError:
        logger.exception("hata")
    for h in logger.handlers:
        h.flush()
    text = (tmp_path / "vea.log").read_text()
    assert "RuntimeError" in text and "abcdefghijkl" not in text
    logging.getLogger("umbrella_vea").handlers.clear()


def test_instance_lock_blocks_second_instance(tmp_path):
    from umbrella_vea.state import InstanceLock

    with InstanceLock(tmp_path):
        with pytest.raises(RuntimeError, match="Başka bir"):
            InstanceLock(tmp_path).acquire()
    InstanceLock(tmp_path).acquire()  # serbest bırakıldıktan sonra alınabilir
