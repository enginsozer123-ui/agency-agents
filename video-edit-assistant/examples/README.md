# Örnek çıktılar

Bu klasördeki dosyalar, `tests/make_sample.py` ile üretilen 24,5 saniyelik örnek videonun işlenmesiyle oluşturuldu. Video 1280x720 boyutunda ve iki sahnelidir; Türkçe konuşması espeak-ng ile sentezlendi.

| Dosya | İçerik |
|---|---|
| `sample_report.json` | `report.json` örneği (yalnızca kaynak dosya yolu göreli yola çevrildi) |
| `sample_full.srt` | Tüm videonun SRT altyazısı |
| `sample_clip_01.srt` | `clip_01` kesitine göre sıfırdan başlayan SRT |

**Önemli:** Bu örnekler üretilirken gerçek Whisper modeli kullanılamadı, çünkü geliştirme ortamında model indirme sunucusuna erişim engelliydi. Transkript adımında `tests/doubles.py` içindeki `ScriptedTranscriber` test dublörü kullanıldı:

- Konuşma aralıklarının zamanları ses dosyasından FFmpeg ile **gerçekten ölçüldü**.
- Metin, örnek videonun bilinen senaryosundan alındı.
- Bu durum raporda `"engine": "test-double"` olarak açıkça yazıyor.

Diğer tüm adımlar gerçek çıktılardır: medya analizi, sessizlik, sahne tespiti, kesit seçimi, 9:16 render ve SRT.

Gerçek Whisper çıktısı almak için kendi Mac'inizde `python -m umbrella_vea process ornek.mp4` komutunu çalıştırın.
