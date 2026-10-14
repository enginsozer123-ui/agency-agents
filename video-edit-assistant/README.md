# Umbrella Dijital — AI Video Edit Assistant (MVP)

Mac'teki `input/` klasörüne bırakılan videoları otomatik olarak analiz eder. Instagram Reels ve YouTube Shorts için **düzenlenebilir 9:16 taslaklar** üretir. Varsayılan olarak tamamen yerel çalışır; ücretli API gerekmez.

## Ne yapar?

1. **İzler:** `input/` klasörüne yeni video gelince algılar. Kopyalama bitmeden başlamaz, aynı videoyu iki kez işlemez.
2. **Analiz eder:** Süre, çözünürlük, FPS ve ses bilgisi (ffprobe). Sessizlikler (FFmpeg `silencedetect`) ve sahne geçişleri (PySceneDetect).
3. **Yazıya döker:** Türkçe konuşmayı kelime zamanlarıyla transkripte çevirir (faster-whisper, yerel). Tam video için SRT üretir.
4. **Kesit önerir:** Net giriş, soru-cevap, güçlü ifade ve temiz kesim noktasına göre aday kesitler seçer. Her aday için başlangıç/bitiş, süre, gerekçe ve güven puanı verir. Gerekçeler yalnızca transkriptte gerçekten geçen ifadelere dayanır.
5. **Taslak üretir:** Her adayı 1080x1920 MP4 olarak dışa aktarır. Kadraj sabit merkez kırpmadır, Türkçe altyazı isteğe bağlı olarak videoya işlenir ve ayrıca `.srt` olarak kaydedilir. Ses seviyesi değiştirilmez, müzik eklenmez.
6. **Rapor yazar:** Her video için ayrı bir çıktı klasörü ve `report.json` oluşturur.

Orijinal video **hiçbir zaman değiştirilmez, taşınmaz veya silinmez**. Sistem yalnızca okur ve işlem sonunda dosyanın boyutunun ve değişiklik zamanının aynı kaldığını doğrular.

## Mimari

```
input/ ──► watcher (yoklama + kararlılık kontrolü + SQLite iş kaydı)
              │
              ▼
           pipeline ──► ffprobe ─ silencedetect ─ PySceneDetect ─ faster-whisper
              │                         │
              │                  candidates (kural tabanlı puanlama)
              │                         │
              │         [isteğe bağlı] cloud/semantic_scoring (yalnızca metin, onaylı)
              ▼
           render (FFmpeg: merkez kırpma → 1080x1920, libass altyazı, AAC ses)
              ▼
output/<video>_<id>/report.json + transcript.json + full.srt + clips/clip_NN.mp4/.srt
```

Tek bir Python süreci olarak çalışır; mikroservis veya sunucu yoktur. Kod `umbrella_vea/` klasöründedir:

| Modül | Görev |
|---|---|
| `watcher.py` | Klasör izleme, kopyalama bitti kontrolü, tekrar işlememe, otomatik yeniden deneme |
| `state.py` | SQLite iş kaydı (`state/jobs.sqlite3`), içerik parmak izi |
| `ffmpeg_tools.py` | ffprobe medya bilgisi, sessizlik tespiti, ses çıkarma |
| `scenes.py` | PySceneDetect ile sahne geçişleri |
| `transcribe.py` | faster-whisper ile Türkçe transkript |
| `srt.py` | SRT üretimi ve kesite göre altyazı kaydırma |
| `candidates.py` | Kesit adayı seçimi ve gerekçeler |
| `render.py` | 9:16 render |
| `pipeline.py` | Tüm adımlar ve JSON rapor |
| `cloud/semantic_scoring.py` | İsteğe bağlı bulut puanlama (varsayılan kapalı) |

## Kurulum (macOS)

Gereksinimler: Python 3.11 veya üstü, FFmpeg (libass ve libx264 ile; Homebrew sürümünde ikisi de var).

```bash
# 1) Araçlar
brew install python@3.12 ffmpeg

# 2) Proje klasörü
cd video-edit-assistant
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt          # yerel mod
# pip install -r requirements-dev.txt    # testler için (pytest)
# pip install -r requirements-cloud.txt  # yalnızca bulut modülü isteniyorsa

# 3) Yapılandırma
cp config.example.toml config.toml
# cp .env.example .env                   # yalnızca bulut modülü için

# 4) Kontrol (ilk seferde Whisper modelini indirir; internet gerekir)
python -m umbrella_vea doctor --check-model
```

Whisper modeli ilk kullanımda HuggingFace'ten indirilir ve önbelleğe alınır (`small` yaklaşık 480 MB). Sonraki çalışmalarda internet gerekmez. İnternet olmayan bir makinede modeli başka bir yerde indirip `config.toml` içindeki `transcribe.model` alanına klasör yolunu yazabilirsiniz.

## Çalıştırma

```bash
source .venv/bin/activate

python -m umbrella_vea watch            # sürekli izle (Ctrl+C ile durdur)
python -m umbrella_vea watch --once     # klasördekileri işle ve çık
python -m umbrella_vea process video.mp4          # tek dosya (input/ dışında da olabilir)
python -m umbrella_vea process video.mp4 --force  # daha önce işlenmişse yeniden işle
python -m umbrella_vea status           # iş durumları ve hatalar
python -m umbrella_vea retry 3          # 3 numaralı başarısız işi tekrar sıraya al
python -m umbrella_vea retry --all      # tüm başarısız işleri tekrar sıraya al
python -m umbrella_vea doctor           # kurulum ve sürüm bilgisi
```

Komutlar `config.toml` dosyasını bulunduğunuz klasörden okur; başka bir dosya için `--config yol/config.toml` verin. Ayrıntılı log için `-v` ekleyin. Tüm loglar `logs/vea.log` dosyasına da yazılır.

Uzun işlemlerde konsolda aşama ve yüzde görünür (`[ 45%] transkripsiyon`, `[ 82%] render clip_02` gibi).

### Klasör izleme kuralları

- **Desteklenen uzantılar:** `.mp4 .mov .m4v .mkv .avi .webm` (`watch.extensions` ile değiştirilebilir).
- **Atlanan dosyalar:** Gizli dosyalar ve geçici indirme dosyaları (`.part`, `.crdownload`, `.download`, `.tmp`).
- **Kopyalama bitti kontrolü:** Dosya boyutu ve değişiklik zamanı `stable_checks` ardışık kontrolde sabit kalmalıdır. Ardından dosya ffprobe ile okunabilmelidir.
- **Tekrar işlememe:** Aynı içerik (adı değişmiş olsa bile) parmak iziyle tanınır ve ikinci kez işlenmez.
- **Hatalar:** Bozuk dosya veya işlem hatası `failed` olarak kaydedilir ve loglanır. Kesitlerden yalnızca bir kısmı render edilemezse de iş `failed` sayılır; rapor `done_with_errors` olarak yazılır, böylece iş yeniden denenebilir. İş, `max_attempts` sınırına kadar otomatik yeniden denenir; sonra `retry` komutuyla tekrar sıraya alınabilir. `retry` ile sıraya alınan işler bir sonraki `watch` çalışmasında işlenir. Bu, `process` ile `input/` dışından eklenen dosyalar için de geçerlidir. Yarıda kesilen işler bir sonraki başlatmada otomatik olarak sıraya alınır.
- **Tek örnek:** Aynı `state/` klasörüyle aynı anda yalnızca bir `watch` veya `process` çalışabilir (`state/vea.lock`).
- **Klasör güvenliği:** Çıktı, durum ve log klasörleri `input/` içinde olamaz. Sembolik bağlar da çözülerek kontrol edilir.

## Çıktılar

```
output/Musteri_Videosu_a5fbb625/
├── report.json        # tüm analiz, adaylar, render sonuçları, uyarılar, sürümler, süreler
├── transcript.json    # segment ve kelime zamanlı tam transkript
├── full.srt           # tüm video için Türkçe altyazı
└── clips/
    ├── clip_01.mp4    # 1080x1920, H.264 + AAC
    ├── clip_01.srt    # kesite göre sıfırdan başlayan altyazı
    └── ...
```

`report.json` içindeki başlıca alanlar:

| Alan | İçerik |
|---|---|
| `media` | Süre, çözünürlük, FPS, kodekler, döndürme, ses kanalları |
| `silences`, `scene_cuts` | Sessizlik aralıkları ve sahne geçiş zamanları |
| `transcript` | Dil, segment sayısı, tam metin, dosya adları |
| `candidates[]` | `id`, `start`, `end`, `duration`, `confidence` (0–1), `basis`, `reasons[]`, `text`, `features` |
| `renders[]` | Her kesitin dosyası, gerçek çözünürlük/süre (ffprobe ile doğrulanmış), altyazı durumu, hata |
| `framing` | `center_crop_fixed`, `face_tracking: false` ve açıklama |
| `audio` | `level_modified: false`, `music_added: false` |
| `cloud` | Bulutun kullanılıp kullanılmadığı, nedeni ve ne gönderildiği |
| `warnings`, `timings_sec`, `versions` | Uyarılar, aşama süreleri, araç ve paket sürümleri |

Örnekler: [`examples/sample_report.json`](examples/sample_report.json), [`examples/sample_full.srt`](examples/sample_full.srt), [`examples/sample_clip_01.srt`](examples/sample_clip_01.srt). Bu örneklerin nasıl üretildiği [`examples/README.md`](examples/README.md) dosyasında yazıyor.

### Güven puanı nasıl hesaplanır?

`confidence`, şeffaf bir kural puanıdır; bir yapay zekâ tahmini değildir:

- **Net giriş (%25):** Konuşmanın ilk cümlesi olması, öncesinde en az 1 sn sessizlik veya bir sahne geçişi bulunması.
- **Soru-cevap (%25):** Kesitte bir soru cümlesi ve ardından gelen yanıt bulunması.
- **Güçlü ifade (%20):** "kesinlikle", "en önemli", "asla" gibi ifadeler veya ünlem.
- **Temiz sınır (%15):** Kesitin cümle başında başlayıp bir duraklamada bitmesi.
- **Süre uyumu (%15):** Hedef süreye (`target_sec`) yakınlık.

Toplam puan, Whisper'ın segment güvenine göre ölçeklenir. Hangi ölçütlerin gerçekten sağlandığı `features` alanında ve Türkçe `reasons` listesinde yazar.

Konuşma yoksa adaylar sahne sınırlarına göre seçilir. Bu durumda puan düşük olur ve `basis: "scenes"` değeri ile gerekçede açıkça belirtilir.

## İsteğe bağlı bulut modülü (anlamsal puanlama)

Yerel puanlamaya ek olarak, aday kesitlerin **transkript metni** Claude'a gönderilip anlamsal olarak puanlatılabilir. Sonuç rapordaki `candidates[].cloud_score` alanına yazılır; yerel puanın üzerine yazılmaz.

Varsayılan olarak **kapalıdır**. Açmak için üç koşulun hepsi gerekir:

```toml
# config.toml
[cloud]
enabled = true
consent_send_transcript = true   # müşteri transkriptinin buluta gönderilmesine açık onay
```

Üçüncü koşul, `.env` içinde `ANTHROPIC_API_KEY=...` tanımlı olmasıdır. Ayrıca `pip install -r requirements-cloud.txt` ile paket kurulmalıdır.

- **Ne gönderilir:** Yalnızca transkript metni ve aday zaman aralıkları. Video ve ses **asla** gönderilmez; modülde medya yükleyen bir kod yolu yoktur. Metin `max_transcript_chars` ile sınırlıdır.
- **Anahtar güvenliği:** API anahtarı kaynak koda, rapora veya loglara yazılmaz; loglarda anahtar benzeri değerler maskelenir.
- **Hata durumu:** Koşullardan biri eksikse veya istek başarısız olursa işlem yerel modda devam eder ve nedeni rapordaki `cloud.reason` alanına yazılır.
- **Model ve maliyet:** Varsayılan model `claude-opus-5-5`'tir; refusal durumunda sunucu tarafı yedek model (`fallbacks: "default"`) etkindir. Her çalıştırma ücretlidir ve maliyet aday metninin uzunluğuna bağlıdır.

## Testler

```bash
pip install -r requirements-dev.txt
python -m pytest                                    # birim + entegrasyon + uçtan uca
VEA_REAL_WHISPER=1 python -m pytest -k real_whisper # gerçek Whisper ile uçtan uca (model indirir)
VEA_REAL_WHISPER=1 VEA_WHISPER_MODEL=tiny python -m pytest -k real_whisper  # daha hızlı
```

Uçtan uca test, `espeak-ng` ile Türkçe konuşmalı ve iki sahneli kısa bir örnek video üretir (`tests/make_sample.py`). Videoyu `input/` klasörüne kopyalar, izleyiciyle işletir ve kabul kriterlerini doğrular. espeak-ng kurulu değilse (`brew install espeak-ng`) bu test atlanır.

Son test sonuçları için [`docs/TEST_RESULTS.md`](docs/TEST_RESULTS.md) dosyasına bakın.

## Bilinen sınırlamalar

- **Yüz takibi yok.** Kadraj sabit merkez kırpmadır. Konuşmacı kenardaysa kadraj dışında kalabilir; rapor bunu `framing` alanında belirtir.
- **Kesit seçimi kural tabanlıdır.** Anlamı "anlamaz"; sinyalleri (soru işareti, güçlü kelime listesi, duraklamalar) kullanır. İroni, bağlam veya görsel içerik değerlendirilmez. Anlamsal değerlendirme bulut modülüyle isteğe bağlıdır.
- **Soru tespiti noktalamaya dayanır.** Whisper'ın soru işaretlerine ve Türkçe soru eklerine (mı/mi/mu/mü, neden, nasıl…) bakılır; noktalama eksikse soru-cevap kaçabilir.
- **Transkript hızı:** Mac'te faster-whisper CPU'da çalışır (CTranslate2 Apple GPU'yu desteklemez). Hız henüz Mac'te ölçülmedi. Daha büyük modeller (`medium`, `large-v3`) daha doğru ama daha yavaştır; ilk denemede `small` önerilir.
- **Kare hızı:** Kesitler kaynağın kare hızında üretilir; değişken kare hızlı (VFR) telefon videolarında FFmpeg varsayılan davranışı geçerlidir.
- **Tek işlem:** Videolar sırayla işlenir; paralel iş kuyruğu yoktur.
- **Duraklayan kopyalama:** Dosya `stable_checks × poll_interval_sec` (varsayılan 6 sn) boyunca hiç büyümezse kopyalama bitti sayılır. Çok yavaş veya duraklayan ağ kopyalamalarında bu değerleri büyütün. Kopyalama sonradan devam ederse, işlem sonundaki kontrol orijinal dosyanın değiştiğini yakalar ve işi başarısız olarak işaretler.
- **Zaman aşımları:** FFmpeg adımlarında (render, ses çıkarma, sessizlik, ffprobe) zaman aşımı vardır. PySceneDetect ve Whisper adımlarında yoktur.
- **Parmak izi:** Tekrar işlememe kontrolü dosya boyutu ile ilk ve son 8 MB'a bakar. Boyutu, başı ve sonu birebir aynı olup yalnızca ortası farklı iki dosya aynı video sayılır; gerçek video dosyalarında bu pek olası değildir.
- **Altyazı fontu:** `subtitle_font` sistemde yoksa libass benzer bir fonta düşer.
- **Kesit hassasiyeti:** Başlangıç ve bitiş yeniden kodlama ile kare hassasiyetinde yapılır. Bu yüzden render, akış kopyalamaya göre daha yavaştır.

## Sonraki adımlar

Premiere Pro XML/EDL dışa aktarımı planı: [`docs/premiere-export-plan.md`](docs/premiere-export-plan.md).
