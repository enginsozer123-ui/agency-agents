# Test sonuçları

- **Tarih:** 2026-10-14
- **Ortam:** Linux bulut konteyneri (x86_64), Python 3.13.16, FFmpeg 6.1.1, faster-whisper 1.2.1, PySceneDetect 0.7.2, espeak-ng 1.51.
- **Mac:** Testler henüz macOS'ta çalıştırılmadı.

## Özet

| Komut | Sonuç |
|---|---|
| `python -m pytest` | **48 geçti, 1 atlandı** |
| `VEA_REAL_WHISPER=1 VEA_WHISPER_MODEL=tiny python -m pytest -k real_whisper` | **BAŞARISIZ.** Bu ortamda model indirilemedi (HuggingFace `403 Forbidden`). Kod hatası değil; iş `failed` olarak kaydedildi ve hata loglandı. Bu test, internete erişimi olan bir Mac'te çalıştırılmalı. |

## Kabul kriterleri

| # | Kriter | Durum | Kanıt |
|---|---|---|---|
| 1 | Test videosu `input/` klasörüne konduğunda algılanıyor | ✅ | `test_e2e_watch_folder`, `test_watcher_waits_until_copy_finishes`; CLI `watch --once` elle çalıştırıldı |
| 2 | Video bilgileri ve Türkçe transkript üretiliyor | ⚠️ Kısmen | Video bilgileri gerçek (ffprobe). Transkript bu ortamda **test dublörüyle** üretildi: zamanlar gerçek ölçüm, metin bilinen senaryo. Gerçek Whisper burada çalıştırılamadı. |
| 3 | En az bir anlamlı kesit adayı zaman kodlarıyla raporlanıyor | ✅ | `test_e2e_watch_folder`, `examples/sample_report.json` |
| 4 | Geçerli bir 9:16 MP4 taslak ve SRT üretiliyor | ✅ | ffprobe ile 1080x1920 ve 9:16 doğrulandı; altyazının videoya işlendiği kare görüntüsünde kontrol edildi |
| 5 | Orijinal dosya korunuyor | ✅ | SHA-256, boyut ve değişiklik zamanı işlem öncesi ve sonrası aynı; `input/` klasörüne hiçbir dosya yazılmadı |
| 6 | İşlem hataları loglanıyor ve testler çalışıyor | ✅ | Bozuk dosya, kısmi render hatası, ses akışı olmayan video ve model yüklenememe durumları test edildi |
| 7 | Bulut API'si olmadan yerel mod çalışıyor | ✅ | Testler `ANTHROPIC_API_KEY` olmadan çalıştı; raporda `cloud.used = false` |

## Elle yapılan ek doğrulamalar

- **Ses seviyesi:** Kaynak aralık ve taslak aynı ölçüldü (`volumedetect`): ortalama −20,9 dB, tepe −1,7 dB.
- **Bozuk H.264 kaynak:** İnceleme bulgusu #1 için 40 saniyelik, ortası rastgele bozulmuş bir video render edildi. Takılma olmadı, 7,9 saniyede tamamlandı.
- **CLI:** `doctor`, `watch --once` (boş klasör ve dolu klasör), `status` ve `retry --all` komutları gerçek dosyalarla çalıştırıldı.
- **Bulut modülü:** Gerçek API'ye **hiç istek gönderilmedi**. Gönderilen içerik sahte bir istemciyle doğrulandı: yalnızca metin gönderiliyor, `refusal` durumu ele alınıyor. Gerçek API ile uçtan uca test yapılmadı.

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
SKIPPED [1] tests/test_e2e.py:120: Gerçek Whisper testi için VEA_REAL_WHISPER=1 (ilk çalıştırmada model internetten indirilir)
======================== 48 passed, 1 skipped in 23.61s ========================
```
