"""viral-reels profili: yapılandırma katmanları, kurgu planı, otomatik süre, kelime vurgulu altyazı,
render doğrulaması ve teslim öncesi inceleme."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from umbrella_vea import candidates as cand
from umbrella_vea.__main__ import main
from umbrella_vea.captions import build_events, chunk_words, to_ass
from umbrella_vea.config import CandidateConfig, CaptionsConfig, ConfigError, EditConfig, available_profiles, load_config
from umbrella_vea.edit import estimate_output_duration, map_segments, plan_clip, silence_cuts
from umbrella_vea.ffmpeg_tools import probe
from umbrella_vea.pipeline import process_video
from umbrella_vea.render import render_clip
from umbrella_vea.review import ReviewError, decide
from umbrella_vea.state import fingerprint
from umbrella_vea.transcribe import Segment, Transcript, Word

from conftest import ROOT, make_video


def _cfg_file(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return p


# --- yapılandırma ---------------------------------------------------------------

def test_defaults_without_profile_are_conservative(tmp_path):
    cfg = load_config(base_dir=tmp_path)
    assert cfg.profile.id == ""
    assert cfg.candidates.duration_mode == "fixed"
    assert not cfg.edit.remove_silence and not cfg.edit.punch_in_zooms
    assert cfg.captions.timing == "segment" and cfg.captions.style == "plain"
    assert cfg.delivery.require_review_before_delivery and not cfg.delivery.auto_publish
    assert cfg.cloud.enabled is False


def test_viral_reels_profile_values(tmp_path):
    assert "viral-reels" in available_profiles()
    cfg = load_config(ROOT / "config.example.toml", base_dir=tmp_path)
    assert cfg.profile.id == "viral-reels"
    r, c, e, cap = cfg.render, cfg.candidates, cfg.edit, cfg.captions
    assert (r.aspect_ratio, r.width, r.height) == ("9:16", 1080, 1920)
    assert (c.duration_mode, c.min_sec, c.max_sec) == ("auto", 15.0, 60.0)
    assert e.remove_silence and e.smart_cuts and e.punch_in_zooms and e.zoom_intensity == "subtle"
    assert cfg.analysis.detect_scenes
    assert cap.enabled and cfg.transcribe.language == "tr"
    assert (cap.timing, cap.style, cap.highlight_active_word) == ("word_level", "dynamic_highlight", True)
    assert cap.export_srt and cap.burn
    assert not cfg.audio.add_music and not cfg.audio.add_sound_effects
    assert not cfg.cloud.enabled and not cfg.cloud.consent_send_transcript
    assert cfg.delivery.preserve_original and not cfg.delivery.auto_publish
    assert cfg.delivery.require_review_before_delivery


def test_user_file_overrides_profile_and_cli_overrides_file(tmp_path):
    p = _cfg_file(tmp_path, '[profile]\nid = "viral-reels"\n[candidates]\nmax_sec = 45.0\n[edit]\npunch_in_zooms = false\n')
    cfg = load_config(p)
    assert cfg.candidates.max_sec == 45.0 and cfg.edit.punch_in_zooms is False
    assert cfg.edit.remove_silence is True  # profilden
    p2 = _cfg_file(tmp_path, "[watch]\nstable_checks = 2\n")
    assert load_config(p2).profile.id == ""
    assert load_config(p2, profile="viral-reels").edit.remove_silence is True


@pytest.mark.parametrize("body,needle", [
    ('[profile]\nid = "yok-boyle"\n', "Bilinmeyen profil 'yok-boyle'"),
    ("[render]\nwidth = 1080\nheight = 1080\n", "aspect_ratio (9:16) ile uyuşmuyor"),
    ('[render]\nwidth = "1080"\n', "int türünde değer bekleniyor"),
    ('[candidates]\nduration_mode = "otomatik"\n', "'auto' veya 'fixed'"),
    ("[candidates]\nmin_sec = 70.0\nmax_sec = 60.0\n", "min_sec (70.0)"),
    ("[audio]\nadd_music = true\n", "add_music = true henüz desteklenmiyor"),
    ("[audio]\nadd_sound_effects = true\n", "add_sound_effects = true henüz desteklenmiyor"),
    ("[delivery]\nauto_publish = true\n", "otomatik yayın özelliği yok"),
    ("[delivery]\npreserve_original = false\n", "orijinal dosya her zaman korunur"),
    ('[captions]\nstyle = "dynamic_highlight"\ntiming = "segment"\n', "timing = 'word_level' gerekir"),
    ('[edit]\nzoom_intensity = "huge"\n', "zoom_intensity şunlardan biri"),
    ("[render]\nburn_subtitles = true\n", "captions.burn kullanın"),
    ("[render\nwidth = 1\n", "TOML sözdizimi hatası"),
    ('[profile]\nid = "viral-reels"\nfoo = 1\n', "yalnızca 'id'"),
    ("[nope]\nx = 1\n", "bilinmeyen yapılandırma bölümü [nope]"),
])
def test_invalid_config_gives_clear_message(tmp_path, body, needle):
    with pytest.raises(ConfigError) as exc:
        load_config(_cfg_file(tmp_path, body))
    assert needle in str(exc.value)


def test_missing_config_file_message(tmp_path):
    with pytest.raises(ConfigError, match="bulunamadı"):
        load_config(tmp_path / "yok.toml")


def test_cli_reports_config_error_and_exit_code(tmp_path, capsys):
    p = _cfg_file(tmp_path, "[audio]\nadd_music = true\n")
    assert main(["--config", str(p), "doctor"]) == 2
    err = capsys.readouterr().err
    assert "add_music" in err and err.count("Yapılandırma hatası") == 1


def test_cli_doctor_shows_profile(tmp_path, capsys):
    p = _cfg_file(tmp_path, '[profile]\nid = "viral-reels"\n')
    main(["--config", str(p), "doctor"])
    out = capsys.readouterr().out
    assert "viral-reels" in out and "1080x1920 (9:16)" in out and "dynamic_highlight" in out


# --- kurgu planı ------------------------------------------------------------------

SIL = [{"start": 4.0, "end": 5.5, "duration": 1.5}, {"start": 9.0, "end": 9.3, "duration": 0.3}]


def _words(start: float, texts: list[str], step: float = 0.5) -> list[Word]:
    return [Word(start + i * step, start + i * step + 0.4, t, 0.9) for i, t in enumerate(texts)]


def _two_sentences() -> list[Segment]:
    w1 = _words(1.0, ["Bu", "ilk", "cümle", "burada."])
    w2 = _words(6.0, ["Peki", "ikinci", "cümle", "nedir?"])
    return [Segment(1.0, w1[-1].end, " ".join(w.text for w in w1), -0.2, 0.01, w1),
            Segment(6.0, w2[-1].end, " ".join(w.text for w in w2), -0.2, 0.01, w2)]


def test_silence_cuts_respect_min_length_and_padding():
    cfg = EditConfig(remove_silence=True, min_cut_silence_sec=0.5, silence_padding_sec=0.1)
    assert silence_cuts(0, 12, SIL, cfg) == [(4.1, 5.4)]  # 0.3 sn'lik duraklama korunur
    assert estimate_output_duration(0, 12, SIL, cfg) == pytest.approx(12 - 1.3)
    assert estimate_output_duration(0, 12, SIL, EditConfig(remove_silence=False)) == 12


def test_plan_clip_cuts_silence_alternates_zoom_and_maps_words():
    segs = _two_sentences()
    cfg = EditConfig(remove_silence=True, punch_in_zooms=True, silence_padding_sec=0.1)
    plan = plan_clip(0.8, 8.2, SIL, segs, cfg, 1.08, has_audio=True)
    assert [(round(p.src_start, 2), round(p.src_end, 2)) for p in plan.pieces] == [(0.8, 4.1), (5.4, 8.2)]
    assert [p.zoom for p in plan.pieces] == [1.0, 1.08]
    assert plan.output_duration == pytest.approx(3.3 + 2.8)
    assert plan.features["remove_silence"]["status"] == "applied"
    assert plan.features["punch_in_zooms"]["status"] == "applied"
    mapped = map_segments(segs, plan)
    # 2. cümlenin ilk kelimesi kaynakta 6.0 sn; çıktı: 3.3 (1. parça) + (6.0 - 5.4)
    assert mapped[1].words[0].start == pytest.approx(3.3 + 0.6)
    assert mapped[0].words[0].start == pytest.approx(0.2)
    assert [w.text for w in mapped[1].words] == ["Peki", "ikinci", "cümle", "nedir?"]


def test_plan_reports_not_applicable_instead_of_silently_skipping():
    segs = [Segment(1.0, 3.0, "Tek cümle.", words=_words(1.0, ["Tek", "cümle."]))]
    plan = plan_clip(0.9, 3.2, [], segs, EditConfig(remove_silence=True, punch_in_zooms=True), 1.08, True)
    assert plan.features["remove_silence"]["status"] == "not_applicable"
    assert plan.features["punch_in_zooms"]["status"] == "not_applicable"
    assert [p.zoom for p in plan.pieces] == [1.0]
    no_audio = plan_clip(0, 3, SIL, segs, EditConfig(remove_silence=True), 1.08, has_audio=False)
    assert no_audio.features["remove_silence"]["status"] == "not_applicable"


# --- otomatik süre -------------------------------------------------------------------

def _long_talk(n: int, sec: float = 4.0, gap: float = 1.0) -> list[Segment]:
    segs, t = [], 0.5
    for i in range(n):
        segs.append(Segment(t, t + sec, f"Cümle numarası {i}.", -0.2, 0.01))
        t += sec + gap
    return segs


def test_auto_duration_picks_windows_inside_target_range():
    segs = _long_talk(30)  # ~150 sn konuşma
    cfg = CandidateConfig(duration_mode="auto", min_sec=15, max_sec=60, max_candidates=5)
    picks = cand.from_transcript(segs, 160.0, [], [], cfg, EditConfig(smart_cuts=True))
    assert picks
    for p in picks:
        assert 15 <= p.output_duration_est <= 60
        assert any("Otomatik süre" in r and "aralığı içinde" in r for r in p.reasons)
        assert p.duration_note == ""


def test_auto_duration_uses_post_edit_length():
    segs = _long_talk(8, sec=2.0, gap=3.0)  # her cümle arası 3 sn sessizlik
    sil = [{"start": s.end, "end": s.end + 3.0, "duration": 3.0} for s in segs]
    cfg = CandidateConfig(duration_mode="auto", min_sec=15, max_sec=60)
    edit = EditConfig(remove_silence=True, smart_cuts=True)
    picks = cand.from_transcript(segs, 45.0, sil, [], cfg, edit)
    # ham süre 15 sn'yi geçse de sessizlik kesilince kısalıyor: hedefe yalnızca kurgu sonrası süreyle bakılır
    for p in picks:
        assert p.output_duration_est < p.duration
        assert p.duration_note or 15 <= p.output_duration_est <= 60


def test_auto_duration_reports_short_source():
    segs = _long_talk(2, sec=3.0)
    cfg = CandidateConfig(duration_mode="auto", min_sec=15, max_sec=60)
    picks = cand.from_transcript(segs, 10.0, [], [], cfg, EditConfig(smart_cuts=True))
    assert len(picks) == 1
    assert "15–60 sn" in picks[0].duration_note and "en uzun bütün bölüm" in picks[0].duration_note


def test_smart_cuts_snap_to_nearby_scene_cut():
    segs = [Segment(2.0, 6.0, "Birinci.", -0.2, 0.01), Segment(9.0, 12.0, "İkinci.", -0.2, 0.01)]
    cfg = CandidateConfig(duration_mode="auto", min_sec=1, max_sec=60)
    on = cand._score_window(segs, 0, 0, 20.0, [], [1.7, 6.3], cfg, EditConfig(smart_cuts=True))
    assert (on.start, on.end) == (1.7, 6.3) and any("Akıllı kesit" in r for r in on.reasons)
    off = cand._score_window(segs, 0, 0, 20.0, [], [1.7, 6.3], cfg, EditConfig(smart_cuts=False))
    assert (off.start, off.end) == (2.0, 6.0)


# --- altyazı ---------------------------------------------------------------------------

def test_word_level_highlight_events_follow_real_word_times():
    words = _words(0.0, ["Bir", "iki", "üç."]) + _words(3.0, ["Dört", "beş"])
    segs = [Segment(0, 1.4, "Bir iki üç.", words=words[:3]), Segment(3.0, 3.9, "Dört beş", words=words[3:])]
    cap = CaptionsConfig(timing="word_level", style="dynamic_highlight", highlight_active_word=True)
    events, timing = build_events(segs, cap)
    assert timing == "word_level" and len(events) == 5
    assert [round(e.start, 2) for e in events] == [w.start for w in words]
    for i, e in enumerate(events):  # her olayda yalnızca aktif kelime vurgulu
        assert e.text.count("\\c&H") == 1
        assert f"\\fscy112}}{words[i].text}{{\\r}}" in e.text
    assert events[2].end <= 3.0  # cümle sonu satırı bir sonraki satırla çakışmaz
    assert [len(c) for c in chunk_words(words, 4)] == [3, 2]


def test_ass_header_matches_render_size_and_escapes_text():
    segs = [Segment(0, 1, "a {b} c\\d", words=_words(0, ["a", "{b}", "c\\d"]))]
    cap = CaptionsConfig(timing="word_level", style="dynamic_highlight", highlight_active_word=True)
    ass = to_ass(build_events(segs, cap)[0], cap, 1080, 1920)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    body = ass.split("[Events]")[1]
    assert "(b)" in body and "{b}" not in body and "c/d" in body


def test_word_level_falls_back_to_segment_and_says_so():
    segs = [Segment(0, 2, "Kelime zamanı yok.")]
    events, timing = build_events(segs, CaptionsConfig(timing="word_level", style="dynamic_highlight"))
    assert timing == "segment" and len(events) == 1


# --- render: 9:16, punch-in, vurgulu altyazı ------------------------------------------

def _frame(path: Path, t: float, w: int = 1080, h: int = 1920) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(int)


def test_render_pieces_output_is_9_16_and_cuts_gap(tmp_path, cfg):
    src = make_video(tmp_path / "src.mp4", 6, "1280x720")
    out = render_clip(src, 0.0, 6.0, tmp_path / "clip.mp4", cfg.render, True,
                      pieces=[(0.0, 2.0, 1.0), (4.0, 6.0, 1.08)])
    info = probe(out)
    assert (info.width, info.height) == (1080, 1920) and info.width * 16 == info.height * 9
    assert abs(info.duration_sec - 4.0) < 0.15  # 2-4 sn arası kesildi
    assert info.has_audio


def test_punch_in_zoom_changes_picture_only_in_zoomed_piece(tmp_path, cfg):
    src = make_video(tmp_path / "src.mp4", 4, "1280x720")
    a = render_clip(src, 0, 4, tmp_path / "a.mp4", cfg.render, True, pieces=[(0, 2, 1.0), (2, 4, 1.0)])
    b = render_clip(src, 0, 4, tmp_path / "b.mp4", cfg.render, True, pieces=[(0, 2, 1.0), (2, 4, 1.08)])
    same = np.abs(_frame(a, 1.0) - _frame(b, 1.0)).mean()
    diff = np.abs(_frame(a, 3.0) - _frame(b, 3.0)).mean()
    assert same < 1.0 and diff > 3.0  # ölçülen: 0.0 ve ~9.9


def test_burned_highlight_color_is_visible(tmp_path, cfg):
    src = make_video(tmp_path / "src.mp4", 3, "1280x720", audio=True)
    words = _words(0.2, ["Merhaba", "dünya"], step=1.2)
    segs = [Segment(0.2, 2.0, "Merhaba dünya", words=words)]
    cap = CaptionsConfig(timing="word_level", style="dynamic_highlight", highlight_active_word=True,
                         highlight_color="#FFD400", font_size=96)
    (tmp_path / "out").mkdir()
    ass = tmp_path / "out" / "c.ass"
    ass.write_text(to_ass(build_events(segs, cap)[0], cap, 1080, 1920), encoding="utf-8")
    out = render_clip(src, 0, 3, tmp_path / "out" / "c.mp4", cfg.render, True, ass)
    def yellow(t: float) -> int:
        f = _frame(out, t)[1300:1800]  # altyazı bölgesi
        return int(((f[..., 0] > 200) & (f[..., 1] > 170) & (f[..., 2] < 90)).sum())

    ref = render_clip(src, 0, 3, tmp_path / "out" / "ref.mp4", cfg.render, True)  # altyazısız
    ref_f = _frame(ref, 0.6)[1300:1800]
    ref_yellow = int(((ref_f[..., 0] > 200) & (ref_f[..., 1] > 170) & (ref_f[..., 2] < 90)).sum())
    assert yellow(0.6) > ref_yellow + 300, "aktif kelime sarı görünmeli"


# --- inceleme / teslim -------------------------------------------------------------------

class _OneSentence:
    def transcribe(self, audio, duration, progress=None):
        w = _words(0.3, ["Bu", "bir", "deneme", "cümlesidir."], step=0.6)
        return Transcript("tr", 1.0, duration, "test-double", "x", [Segment(0.3, w[-1].end, "Bu bir deneme cümlesidir.", -0.2, 0.01, w)])


def _viral_cfg(cfg):
    from umbrella_vea.config import load_profile

    load_profile(cfg, "viral-reels")
    cfg.render.preset = "ultrafast"
    return cfg


def test_review_is_required_before_delivery(cfg, tmp_path):
    cfg = _viral_cfg(cfg)
    src = Path(shutil.copy(make_video(tmp_path / "k.mp4", 4), cfg.input_dir / "v.mp4"))
    out_dir, report = process_video(src, cfg, fingerprint(src), transcriber=_OneSentence())
    assert report["review"]["status"] == "pending"
    assert not (out_dir / "delivery").exists(), "onay olmadan teslim klasörü oluşmamalı"
    assert report["publishing"]["auto_publish"] is False
    with pytest.raises(ReviewError):
        decide(out_dir, cfg, approve=True, clip_ids=["clip_99"])
    rv = decide(out_dir, cfg, approve=True)
    assert rv["status"] == "approved" and rv["delivered"] == ["clip_01"]
    assert (out_dir / "delivery" / "clip_01.mp4").exists() and (out_dir / "delivery" / "clip_01.srt").exists()
    assert json.loads((out_dir / "report.json").read_text())["review"]["status"] == "approved"
    with pytest.raises(ReviewError, match="zaten incelenmiş"):
        decide(out_dir, cfg, approve=False)


def test_reject_delivers_nothing(cfg, tmp_path):
    cfg = _viral_cfg(cfg)
    src = Path(shutil.copy(make_video(tmp_path / "k.mp4", 4), cfg.input_dir / "v.mp4"))
    out_dir, _ = process_video(src, cfg, fingerprint(src), transcriber=_OneSentence())
    assert decide(out_dir, cfg, approve=False, note="kadraj kötü")["status"] == "rejected"
    assert not (out_dir / "delivery").exists()


def test_without_review_requirement_drafts_are_delivered(cfg, tmp_path):
    cfg = _viral_cfg(cfg)
    cfg.delivery.require_review_before_delivery = False
    src = Path(shutil.copy(make_video(tmp_path / "k.mp4", 4), cfg.input_dir / "v.mp4"))
    out_dir, report = process_video(src, cfg, fingerprint(src), transcriber=_OneSentence())
    assert report["review"]["status"] == "not_required" and report["review"]["delivered"] == ["clip_01"]
    assert (out_dir / "delivery" / "clip_01.mp4").exists()


def test_report_lists_profile_and_feature_status(cfg, tmp_path):
    cfg = _viral_cfg(cfg)
    src = Path(shutil.copy(make_video(tmp_path / "k.mp4", 4), cfg.input_dir / "v.mp4"))
    _, report = process_video(src, cfg, fingerprint(src), transcriber=_OneSentence())
    assert report["profile"]["id"] == "viral-reels" and report["profile"]["width"] == 1080
    assert report["features"]["detect_scenes"]["status"] == "applied"
    r = report["renders"][0]
    assert r["aspect_ratio"] == "9:16" and (r["width"], r["height"]) == (1080, 1920)
    f = r["features"]
    assert f["captions"]["status"] == "applied" and "dynamic_highlight" in f["captions"]["detail"]
    # 4 sn'lik tek cümlede kesilecek duraklama / geçiş yok: "uygulandı" gibi gösterilmemeli
    assert f["punch_in_zooms"]["status"] == "not_applicable"
    assert r["duration_note"]  # 15 sn altı: nedeni raporda
    assert report["audio"]["music_added"] is False and report["audio"]["sound_effects_added"] is False
