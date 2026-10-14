# Test sonuçları

- **Tarih:** 2026-10-14
- **Kapsam:** `viral-reels` profili entegrasyonu dahil.
- **Ortam:** Linux bulut konteyneri (x86_64), Python 3.13.16, FFmpeg 6.1.1 (libass, libx264), faster-whisper 1.2.1, PySceneDetect 0.7.2, espeak-ng 1.51.
- **Mac:** Testler henüz macOS'ta çalıştırılmadı.

## Özet

| Komut | Sonuç |
|---|---|
| `python -m pytest` | **86 geçti, 1 atlandı** (atlanan: gerçek Whisper testi) |
| `VEA_REAL_WHISPER=1 VEA_WHISPER_MODEL=tiny python -m pytest -k real_whisper` | **BAŞARISIZ: doğrulanmadı.** Model indirilemedi (HuggingFace `403 Forbidden`). İş `failed` olarak kaydedildi ve hata loglandı. Bu test internete erişimi olan bir Mac'te çalıştırılmalı. |

**Gerçek Whisper hakkında:** Türkçe transkripsiyon ve kelime zamanları bu ortamda gerçek modelle **hiç doğrulanmadı**. Uçtan uca testte ve örnek çıktılarda `ScriptedTranscriber` test dublörü kullanıldı:
- Konuşma aralıkları sesten FFmpeg ile ölçüldü.
- Metin bilinen senaryodan alındı.
- Kelime zamanları, her cümlenin süresine eşit aralıklarla dağıtıldı.

Bu sonuçlar gerçek Whisper başarısı **sayılmamalıdır**.

## viral-reels gereksinimleri

| Gereksinim | Durum | Kanıt |
|---|---|---|
| Profil CLI ve işleme hattında yükleniyor | ✅ | `test_viral_reels_profile_values`, `test_user_file_overrides_profile_and_cli_overrides_file`, `test_cli_doctor_shows_profile`; rapordaki `profile` alanı |
| Çıktı 1080x1920 ve 9:16 | ✅ | `test_render_pieces_output_is_9_16_and_cuts_gap`, `test_report_lists_profile_and_feature_status`, uçtan uca test (ffprobe ile) |
| Otomatik süre 15–60 sn; uyulamazsa neden raporlanıyor | ✅ | `test_auto_duration_*`; uçtan uca testte taslak 17,8 sn; kısa kaynakta `duration_note` yazılıyor |
| Altyazı kelime zamanlarını kullanıyor | ✅ (test dublörü zamanlarıyla) | `test_word_level_highlight_events_follow_real_word_times`, `test_plan_clip_cuts_silence_alternates_zoom_and_maps_words` (kelimeler kurgu sonrası zamana doğru taşınıyor) |
| Aktif kelime vurgusu (ASS) | ✅ | `test_burned_highlight_color_is_visible` (render edilmiş karede sarı piksel sayımı), elle kare kontrolü |
| Sessizlik kaldırma | ✅ | `test_silence_cuts_*`, `test_render_pieces_output_is_9_16_and_cuts_gap` (2 sn boşluk çıktıdan çıkarıldı) |
| Sahne algılama, akıllı kesit | ✅ | `test_smart_cuts_snap_to_nearby_scene_cut`; rapordaki `features.detect_scenes` |
| Hafif punch-in | ✅ | `test_punch_in_zoom_changes_picture_only_in_zoomed_piece`: zoom'suz parçada piksel farkı 0,0, %8 zoom'lu parçada yaklaşık 9,9 |
| Uygulanamayan özellik raporlanıyor | ✅ | `test_plan_reports_not_applicable_instead_of_silently_skipping` |
| Müzik, ses efekti ve otomatik yayın kapalı; açılırsa anlaşılır hata | ✅ | `test_invalid_config_gives_clear_message` (15 hatalı yapılandırma durumu), `test_cli_reports_config_error_and_exit_code` |
| Teslim öncesi inceleme zorunlu | ✅ | `test_review_is_required_before_delivery`, `test_reject_delivers_nothing`; uçtan uca testte CLI ile `review approve` |
| Orijinal dosya korunuyor | ✅ | Uçtan uca test: SHA-256, boyut ve değişiklik zamanı aynı; `input/` klasörüne hiçbir dosya yazılmadı |
| Ses seviyesi değişmiyor | ✅ (önceki ölçüm) | Ses filtre grafiğinde yalnızca `atrim`/`concat` var |

## Elle yapılan doğrulamalar

- **Örnek video (`viral-reels`, test dublörü transkript):**
  - Kaynakta 22,1 sn olan kesitten 4 duraklama (4,34 sn) kesildi; taslak 17,8 sn.
  - 4 parçanın 2'sinde %8 punch-in uygulandı.
  - Çıkarılan karelerde aktif kelimenin ("size", "ilk") sarı ve büyük olduğu görüldü.
- **CLI:** `--profile viral-reels doctor`, `watch --once`, `review list` ve `review approve 1 --clips clip_01` gerçek dosyalarla çalıştırıldı. `delivery/` klasörü yalnızca onaydan sonra oluştu.

## Test listesi (pytest -v)

```
tests/test_e2e.py::test_e2e_watch_folder PASSED
tests/test_e2e.py::test_e2e_real_whisper SKIPPED (Gerçek Whisper tes...)
tests/test_integration.py::test_render_produces_1080x1920[640x360-True] PASSED
tests/test_integration.py::test_render_produces_1080x1920[360x640-True] PASSED
tests/test_integration.py::test_render_produces_1080x1920[480x480-False] PASSED
tests/test_integration.py::test_render_failure_raises_and_leaves_no_partial_file PASSED
tests/test_integration.py::test_output_inside_input_is_rejected PASSED
tests/test_integration.py::test_watcher_waits_until_copy_finishes PASSED
tests/test_integration.py::test_watcher_ignores_temp_hidden_and_unsupported_files PASSED
tests/test_integration.py::test_same_video_is_processed_once_even_if_renamed PASSED
tests/test_integration.py::test_corrupt_file_is_logged_as_failed PASSED
tests/test_integration.py::test_failed_job_retries_up_to_limit_then_manual_retry PASSED
tests/test_integration.py::test_empty_input_folder_is_handled PASSED
tests/test_integration.py::test_cloud_disabled_by_default_and_requires_consent_and_key PASSED
tests/test_integration.py::test_cloud_sends_only_transcript_text PASSED
tests/test_integration.py::test_cloud_refusal_raises PASSED
tests/test_integration.py::test_video_without_audio_still_produces_draft PASSED
tests/test_integration.py::test_state_or_logs_inside_input_are_rejected[state_dir] PASSED
tests/test_integration.py::test_state_or_logs_inside_input_are_rejected[log_dir] PASSED
tests/test_integration.py::test_output_symlink_into_input_is_rejected PASSED
tests/test_integration.py::test_partial_render_failure_is_retryable PASSED
tests/test_integration.py::test_retry_runs_job_registered_outside_input PASSED
tests/test_integration.py::test_cloud_reason_is_labelled_as_model_output PASSED
tests/test_profile.py::test_defaults_without_profile_are_conservative PASSED
tests/test_profile.py::test_viral_reels_profile_values PASSED
tests/test_profile.py::test_user_file_overrides_profile_and_cli_overrides_file PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[profile]\nid = "yok-boyle"\n-Bilinmeyen profil 'yok-boyle'] PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[render]\nwidth = 1080\nheight = 1080\n-aspect_ratio (9:16) ile uyu\u015fmuyor] PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[render]\nwidth = "1080"\n-int t\xfcr\xfcnde de\u011fer bekleniyor] PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[candidates]\nduration_mode = "otomatik"\n-'auto' veya 'fixed'] PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[candidates]\nmin_sec = 70.0\nmax_sec = 60.0\n-min_sec (70.0)] PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[audio]\nadd_music = true\n-add_music = true hen\xfcz desteklenmiyor] PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[audio]\nadd_sound_effects = true\n-add_sound_effects = true hen\xfcz desteklenmiyor] PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[delivery]\nauto_publish = true\n-otomatik yay\u0131n \xf6zelli\u011fi yok] PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[delivery]\npreserve_original = false\n-orijinal dosya her zaman korunur] PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[captions]\nstyle = "dynamic_highlight"\ntiming = "segment"\n-timing = 'word_level' gerekir] PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[edit]\nzoom_intensity = "huge"\n-zoom_intensity \u015funlardan biri] PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[render]\nburn_subtitles = true\n-captions.burn kullan\u0131n] PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[render\nwidth = 1\n-TOML s\xf6zdizimi hatas\u0131] PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[profile]\nid = "viral-reels"\nfoo = 1\n-yaln\u0131zca 'id'] PASSED
tests/test_profile.py::test_invalid_config_gives_clear_message[[nope]\nx = 1\n-bilinmeyen yap\u0131land\u0131rma b\xf6l\xfcm\xfc [nope]] PASSED
tests/test_profile.py::test_missing_config_file_message PASSED
tests/test_profile.py::test_cli_reports_config_error_and_exit_code PASSED
tests/test_profile.py::test_cli_doctor_shows_profile PASSED
tests/test_profile.py::test_silence_cuts_respect_min_length_and_padding PASSED
tests/test_profile.py::test_plan_clip_cuts_silence_alternates_zoom_and_maps_words PASSED
tests/test_profile.py::test_plan_reports_not_applicable_instead_of_silently_skipping PASSED
tests/test_profile.py::test_auto_duration_picks_windows_inside_target_range PASSED
tests/test_profile.py::test_auto_duration_uses_post_edit_length PASSED
tests/test_profile.py::test_auto_duration_reports_short_source PASSED
tests/test_profile.py::test_smart_cuts_snap_to_nearby_scene_cut PASSED
tests/test_profile.py::test_word_level_highlight_events_follow_real_word_times PASSED
tests/test_profile.py::test_ass_header_matches_render_size_and_escapes_text PASSED
tests/test_profile.py::test_word_level_falls_back_to_segment_and_says_so PASSED
tests/test_profile.py::test_render_pieces_output_is_9_16_and_cuts_gap PASSED
tests/test_profile.py::test_punch_in_zoom_changes_picture_only_in_zoomed_piece PASSED
tests/test_profile.py::test_burned_highlight_color_is_visible PASSED
tests/test_profile.py::test_review_is_required_before_delivery PASSED
tests/test_profile.py::test_reject_delivers_nothing PASSED
tests/test_profile.py::test_without_review_requirement_drafts_are_delivered PASSED
tests/test_profile.py::test_report_lists_profile_and_feature_status PASSED
tests/test_units.py::test_example_config_loads_and_resolves_relative_paths PASSED
tests/test_units.py::test_unknown_config_key_is_rejected PASSED
tests/test_units.py::test_srt_timestamp_format PASSED
tests/test_units.py::test_srt_output_and_wrapping PASSED
tests/test_units.py::test_long_segment_split_by_words PASSED
tests/test_units.py::test_clip_cues_shift_to_clip_start PASSED
tests/test_units.py::test_turkish_question_detection[Peki neden bu kadar \xf6nemli?-True] PASSED
tests/test_units.py::test_turkish_question_detection[Bunu biliyor musunuz-True] PASSED
tests/test_units.py::test_turkish_question_detection[Nas\u0131l ba\u015flamal\u0131y\u0131z-True] PASSED
tests/test_units.py::test_turkish_question_detection[Bug\xfcn hava \xe7ok g\xfczel.-False] PASSED
tests/test_units.py::test_strong_phrase_detection_is_turkish_case_aware PASSED
tests/test_units.py::test_candidates_from_transcript_have_times_reasons_and_confidence PASSED
tests/test_units.py::test_candidates_do_not_overlap_beyond_limit PASSED
tests/test_units.py::test_no_speech_falls_back_to_scenes_with_low_confidence PASSED
tests/test_units.py::test_transcript_roundtrip PASSED
tests/test_units.py::test_parse_silencedetect_including_trailing_silence PASSED
tests/test_units.py::test_probe_rejects_corrupt_file PASSED
tests/test_units.py::test_crop_filter_targets_9_16 PASSED
tests/test_units.py::test_fingerprint_ignores_name_but_not_content PASSED
tests/test_units.py::test_jobstore_dedup_fail_retry_and_recovery PASSED
tests/test_units.py::test_api_key_is_redacted_from_logs PASSED
tests/test_units.py::test_question_followed_by_question_is_not_qa PASSED
tests/test_units.py::test_speech_running_to_video_end_is_not_called_clean_end PASSED
tests/test_units.py::test_fallback_reason_matches_the_data PASSED
tests/test_units.py::test_traceback_is_redacted PASSED
tests/test_units.py::test_instance_lock_blocks_second_instance PASSED
SKIPPED [1] tests/test_e2e.py:145: Gerçek Whisper testi için VEA_REAL_WHISPER=1 (ilk çalıştırmada model internetten indirilir)
======================== 86 passed, 1 skipped in 39.87s ========================
```
