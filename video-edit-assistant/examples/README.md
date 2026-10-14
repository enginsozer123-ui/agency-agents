# Örnek çıktılar

Bu klasördeki dosyalar, `tests/make_sample.py` ile üretilen 24,5 saniyelik örnek videonun `viral-reels` profiliyle (`config.example.toml`) işlenmesiyle oluşturuldu. Video 1280x720 boyutunda ve iki sahnelidir; Türkçe konuşması espeak-ng ile sentezlendi.

| Dosya | İçerik |
|---|---|
| `sample_report.json` | `report.json` örneği (yalnızca kaynak dosya yolu göreli yola çevrildi) |
| `sample_full.srt` | Tüm videonun SRT altyazısı |
| `sample_clip_01.srt` | `clip_01` için SRT; zamanlar sessizlik kesildikten sonraki zaman çizelgesine göredir |
| `sample_clip_01.ass` | `clip_01` videosuna işlenen, kelime zamanlı ve aktif kelime vurgulu ASS altyazı |

Örnekte kesit kaynakta 22,1 sn sürüyor. Dört duraklama (toplam 4,34 sn) kesildiği için taslak 17,8 sn; bu, 15–60 sn hedefinin içinde. Dört parçanın ikisinde %8 punch-in uygulandı.

**Önemli:** Bu örnekler üretilirken gerçek Whisper modeli kullanılamadı, çünkü geliştirme ortamında model indirme sunucusuna erişim engelliydi. Transkript adımında `tests/doubles.py` içindeki `ScriptedTranscriber` test dublörü kullanıldı:

- Konuşma aralıklarının zamanları ses dosyasından FFmpeg ile **gerçekten ölçüldü**.
- Metin, örnek videonun bilinen senaryosundan alındı.
- Kelime zamanları her cümlenin süresine eşit aralıklarla dağıtıldı. Bu yüzden ASS dosyasındaki kelime zamanları gerçek Whisper kelime zamanları **değildir**.
- Bu durum raporda `"engine": "test-double"` olarak açıkça yazıyor.

Diğer tüm adımlar gerçek çıktılardır: medya analizi, sessizlik, sahne tespiti, kesit seçimi, 9:16 render ve SRT.

Gerçek Whisper çıktısı almak için kendi Mac'inizde `python -m umbrella_vea process ornek.mp4` komutunu çalıştırın.
