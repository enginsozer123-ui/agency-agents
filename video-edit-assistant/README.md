# Umbrella Dijital — AI Video Edit Assistant (MVP)

Mac'teki `input/` klasörüne bırakılan videoları otomatik olarak analiz eder. Instagram Reels ve YouTube Shorts için **düzenlenebilir 9:16 taslaklar** üretir. Varsayılan olarak tamamen yerel çalışır; ücretli API gerekmez.

Örnek yapılandırma `viral-reels` kurgu profilini kullanır. Profilin ne yaptığı ve hangi özelliklerin henüz geliştirilmediği aşağıdaki [Kurgu profili: viral-reels](#kurgu-profili-viral-reels) bölümünde.

## Ne yapar?

1. **İzler:** `input/` klasörüne yeni video gelince algılar. Kopyalama bitmeden başlamaz, aynı videoyu iki kez işlemez.
2. **Analiz eder:** Süre, çözünürlük, FPS ve ses bilgisi (ffprobe). Sessizlikler (FFmpeg `silencedetect`) ve sahne geçişleri (PySceneDetect).
3. **Yazıya döker:** Türkçe konuşmayı kelime zamanlarıyla transkripte çevirir (faster-whisper, yerel). Tam video için SRT üretir.
4. **Kesit önerir:** Net giriş, soru-cevap, güçlü ifade ve temiz kesim noktasına göre aday kesitler seçer. Her aday için başlangıç/bitiş, süre, gerekçe ve güven puanı verir. Gerekçeler yalnızca transkriptte gerçekten geçen ifadelere dayanır.
5. **Kurgular:** Kesit içindeki duraklamaları çıkarır (jump cut). Parça geçişlerinde hafif punch-in yakınlaştırma yapar.
6. **Taslak üretir:** Her adayı 1080x1920 MP4 olarak dışa aktarır. Kadraj sabit merkez kırpmadır.
   - Türkçe altyazı kelime zamanlıdır; o an konuşulan kelime vurgulanır (ASS/libass ile videoya işlenir).
   - Altyazı ayrıca `.srt` olarak da kaydedilir.
   - Ses seviyesi değiştirilmez, müzik veya ses efekti eklenmez.
7. **Rapor yazar:** Her video için ayrı bir çıktı klasörü ve `report.json` oluşturur. Raporda her özelliğin gerçekte uygulanıp uygulanmadığı yazar.
8. **İncelemeyi bekler:** Taslaklar onaylanmadan teslim klasörüne kopyalanmaz. Otomatik yayın yoktur.

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
           edit (sessizlik kesme + punch-in parçaları + zaman eşleme)
              ▼
           render (FFmpeg: trim/concat → merkez kırpma → 1080x1920 → ASS altyazı, AAC ses)
              ▼
output/<video>_<id>/report.json + transcript.json + full.srt + clips/clip_NN.mp4/.srt/.ass
              ▼
           review (onay) → output/<video>_<id>/delivery/
```

Tek bir Python süreci olarak çalışır; mikroservis veya sunucu yoktur. Kod `umbrella_vea/` klasöründedir:

| Modül | Görev |
|---|---|
| `watcher.py` | Klasör izleme, kopyalama bitti kontrolü, tekrar işlememe, otomatik yeniden deneme |
| `state.py` | SQLite iş kaydı (`state/jobs.sqlite3`), içerik parmak izi |
| `ffmpeg_tools.py` | ffprobe medya bilgisi, sessizlik tespiti, ses çıkarma |
| `scenes.py` | PySceneDetect ile sahne geçişleri |
| `transcribe.py` | faster-whisper ile Türkçe transkript |
| `srt.py` | SRT üretimi |
| `candidates.py` | Kesit adayı seçimi, otomatik süre, akıllı kesit ve gerekçeler |
| `edit.py` | Sessizlik kesme, punch-in parçaları, kaynak→çıktı zaman eşlemesi |
| `captions.py` | Kelime zamanlı, aktif kelime vurgulu ASS altyazı |
| `render.py` | 9:16 render (FFmpeg filtre grafiği) |
| `review.py` | Teslim öncesi inceleme ve `delivery/` klasörü |
| `config.py`, `profiles/` | TOML yapılandırma şeması, profiller ve doğrulama |
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

# 3) Yapılandırma (viral-reels profili hazır gelir)
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
python -m umbrella_vea review list               # incelemeyi bekleyen taslaklar
python -m umbrella_vea review approve 3           # 3 numaralı işin tüm taslaklarını onayla → delivery/
python -m umbrella_vea review approve 3 --clips clip_01,clip_03 --note "ilk ikisi yeterli"
python -m umbrella_vea review reject 3 --note "kadraj uygun değil"
python -m umbrella_vea doctor           # kurulum, profil ve sürüm bilgisi
python -m umbrella_vea --profile viral-reels watch  # profili komut satırından seç
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

## Kurgu profili: viral-reels

Profil, uygulamanın kendi TOML şemasının bir katmanıdır. Değerleri tek bir dosyada durur: [`umbrella_vea/profiles/viral-reels.toml`](umbrella_vea/profiles/viral-reels.toml).

Katmanlar sırasıyla şöyledir; her katman bir öncekini ezer:
1. Kod varsayılanları
2. Profil dosyası
3. Sizin `config.toml` dosyanız
4. Komut satırındaki `--profile`

Hatalı veya desteklenmeyen bir ayar, işlem başlamadan anlaşılır bir mesajla reddedilir.

| İstenen ayar | Şemadaki karşılığı | Durum |
|---|---|---|
| `profile_id = "viral-reels"` | `[profile] id` | ✅ |
| `aspect_ratio = "9:16"`, `width = 1080`, `height = 1920` | `[render] aspect_ratio, width, height` | ✅ Çıktı ffprobe ile doğrulanır; boyut ile oran uyuşmazsa hata verilir |
| `duration_mode = "auto"`, 15–60 sn | `[candidates] duration_mode, min_sec, max_sec` | ✅ Süre, sessizlik kesildikten sonraki süreye göre seçilir; uyulamazsa nedeni `duration_note` alanına yazılır |
| `remove_silence = true` | `[edit] remove_silence` (+ `min_cut_silence_sec`, `silence_padding_sec`) | ✅ Kesit içindeki ≥ 0,5 sn duraklamalar kesilir; iki yanda 0,12 sn pay kalır |
| `detect_scenes = true` | `[analysis] detect_scenes` | ✅ PySceneDetect |
| `smart_cuts = true` | `[edit] smart_cuts` | ✅ Sınırlar cümle sonlarına (nefes payıyla) ve 0,5 sn içindeki sahne geçişlerine hizalanır |
| `punch_in_zooms = true`, `zoom_intensity = "subtle"` | `[edit] punch_in_zooms, zoom_intensity` | ✅ Parça geçişlerinde dönüşümlü %8 merkez yakınlaştırma (`medium` = %15). Geçiş noktası yoksa uygulanmaz ve raporlanır |
| `captions_enabled`, `caption_language = "tr"` | `[captions] enabled`, `[transcribe] language` | ✅ |
| `caption_timing = "word_level"` | `[captions] timing` | ✅ faster-whisper kelime zamanları kullanılır. Kelime zamanı yoksa segment zamanına düşülür ve bu raporlanır |
| `caption_style = "dynamic_highlight"`, `highlight_active_word = true` | `[captions] style, highlight_active_word, highlight_color` | ✅ ASS altyazı: satır görünür, aktif kelime renkli ve %12 büyük |
| `export_srt`, `burn_captions` | `[captions] export_srt, burn` | ✅ SRT ayrıca kaydedilir. Videoya ASS işlenir; `.ass` dosyası da saklanır |
| `add_music = false`, `add_sound_effects = false` | `[audio] add_music, add_sound_effects` | ✅ Kapalı. `true` yapılırsa "henüz desteklenmiyor" hatası verilir |
| `cloud_analysis_enabled = false` | `[cloud] enabled` | ✅ Kapalı |
| `require_consent_for_cloud_upload = true` | `[cloud] consent_send_transcript` | ✅ Onay her zaman zorunlu; kapatılabilen bir ayar değildir |
| `preserve_original = true` | `[delivery] preserve_original` | ✅ `false` yapılırsa hata verilir |
| `auto_publish = false` | `[delivery] auto_publish` | ✅ Yayın özelliği yok; `true` yapılırsa hata verilir |
| `require_review_before_delivery = true` | `[delivery] require_review_before_delivery` | ✅ Onay olmadan `delivery/` oluşmaz |

Profil, eski sürümdeki bazı ayar adlarını değiştirdi. Eski bir `config.toml` kullanılıyorsa uygulama yeni adı söyleyen bir hata verir:

| Eski ad | Yeni ad |
|---|---|
| `render.burn_subtitles` | `captions.burn` |
| `render.write_srt` | `captions.export_srt` |
| `render.subtitle_font` | `captions.font` |
| `render.subtitle_font_size` | `captions.font_size` |

### Henüz geliştirilmeyenler

- **Yüz veya konuşmacı takibi:** Punch-in ve kırpma her zaman merkezdendir.
- **Müzik, ses efekti ve otomatik ses seviyesi düzeltme (normalizasyon):** Yok.
- **Otomatik yayın:** Instagram/YouTube'a yükleme yoktur ve bilerek eklenmedi.
- **Bulut tabanlı anlamsal kesit seçimi:** Yalnızca isteğe bağlı puanlama var; kesitleri yerel kurallar seçer.
- **İnceleme arayüzü:** İnceleme komut satırındandır; bir web arayüzü yok.

## Çıktılar

```
output/Musteri_Videosu_a5fbb625/
├── report.json        # analiz, adaylar, kurgu planı, özellik durumları, inceleme, uyarılar, sürümler
├── transcript.json    # segment ve kelime zamanlı tam transkript
├── full.srt           # tüm video için Türkçe altyazı (kaynak zaman çizelgesi)
├── clips/
│   ├── clip_01.mp4    # 1080x1920 (9:16), H.264 + AAC
│   ├── clip_01.srt    # kurgulanmış kesitin zaman çizelgesine göre altyazı
│   ├── clip_01.ass    # videoya işlenen kelime vurgulu altyazı
│   └── ...
└── delivery/          # yalnızca 'review approve' sonrası (veya inceleme zorunlu değilse)
```

`report.json` içindeki başlıca alanlar:

| Alan | İçerik |
|---|---|
| `media` | Süre, çözünürlük, FPS, kodekler, döndürme, ses kanalları |
| `silences`, `scene_cuts` | Sessizlik aralıkları ve sahne geçiş zamanları |
| `transcript` | Dil, segment sayısı, tam metin, dosya adları |
| `candidates[]` | `id`, `start`, `end`, `duration`, `confidence` (0–1), `basis`, `reasons[]`, `text`, `features` |
| `profile` | Bu çalıştırmada gerçekten kullanılan profil ve ayarlar |
| `features` | Sahne algılama, akıllı kesit ve süre modu için durum: `applied`, `disabled`, `not_applicable` veya `failed` |
| `renders[]` | Her kesit için: dosya, ffprobe ile doğrulanmış çözünürlük/oran/süre, `edit` (parçalar, kesilen sessizlikler), `features` (sessizlik kesme, punch-in, altyazı durumları), `duration_note`, hata |
| `review` | İnceleme gerekli mi; durumu (`pending`, `approved`, `rejected`, `not_required`) ve teslim edilen kesitler |
| `publishing` | `auto_publish: false` |
| `framing` | `center_crop_fixed`, `face_tracking: false` ve açıklama |
| `audio` | `level_modified: false`, `music_added: false`, `sound_effects_added: false` |
| `cloud` | Bulutun kullanılıp kullanılmadığı, nedeni ve ne gönderildiği |
| `warnings`, `timings_sec`, `versions` | Uyarılar, aşama süreleri, araç ve paket sürümleri |

Örnekler: [`examples/sample_report.json`](examples/sample_report.json), [`examples/sample_full.srt`](examples/sample_full.srt), [`examples/sample_clip_01.srt`](examples/sample_clip_01.srt), [`examples/sample_clip_01.ass`](examples/sample_clip_01.ass). Bu örneklerin nasıl üretildiği [`examples/README.md`](examples/README.md) dosyasında yazıyor.

### Güven puanı nasıl hesaplanır?

`confidence`, şeffaf bir kural puanıdır; bir yapay zekâ tahmini değildir:

- **Net giriş (%25):** Konuşmanın ilk cümlesi olması, öncesinde en az 1 sn sessizlik veya bir sahne geçişi bulunması.
- **Soru-cevap (%25):** Kesitte bir soru cümlesi ve ardından gelen yanıt bulunması.
- **Güçlü ifade (%20):** "kesinlikle", "en önemli", "asla" gibi ifadeler veya ünlem.
- **Temiz sınır (%15):** Kesitin cümle başında başlayıp bir duraklamada bitmesi.
- **Süre uyumu (%15):** `auto` modunda kurgu sonrası sürenin `min_sec`–`max_sec` aralığında olması; `fixed` modunda `target_sec` değerine yakınlık.

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
- **Altyazı fontu:** `captions.font` sistemde yoksa libass benzer bir fonta düşer.
- **Jump cut sesi:** Sessizlik kesimlerinde ses yalnızca kesilip birleştirilir; seviyeye dokunmamak için geçişlerde ses yumuşatması (fade) yapılmaz. Nadiren hafif bir "tık" duyulabilir.
- **Punch-in deseni:** Yakınlaştırma, içerikten bağımsız olarak parça geçişlerinde dönüşümlüdür; vurgulu kelimeye göre seçilmez.
- **Kesit hassasiyeti:** Başlangıç ve bitiş yeniden kodlama ile kare hassasiyetinde yapılır. Bu yüzden render, akış kopyalamaya göre daha yavaştır.

## Sonraki adımlar

Geliştirme planı (viral-reels sonraki adımları ve Premiere Pro XML/EDL dışa aktarımı): [`docs/premiere-export-plan.md`](docs/premiere-export-plan.md).
