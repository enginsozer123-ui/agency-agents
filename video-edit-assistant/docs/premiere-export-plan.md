# Geliştirme planı

## A. viral-reels profili: sonraki adımlar

Şu an çalışanlar: 9:16 / 1080x1920 çıktı, otomatik süre (15–60 sn), sessizlik kesme, sahne algılama, akıllı kesit, dönüşümlü hafif punch-in, kelime zamanlı vurgulu ASS altyazı, SRT dışa aktarımı ve teslim öncesi inceleme. Ayrıntılar README'deki profil tablosunda.

Öncelik sırasıyla geliştirilecekler:

1. **Gerçek Türkçe Whisper doğrulaması (öncelikli):** Mac'te `VEA_REAL_WHISPER=1` testi gerçek konuşmalı birkaç müşteri videosuyla çalıştırılacak. Ölçülecekler: kelime zamanlarının doğruluğu (vurgunun sese oturup oturmadığı), soru işareti oranı ve işlem süresi.
2. **Jump cut ses yumuşatması:** Kesim noktalarında 5–10 ms'lik çapraz geçiş. Ses seviyesini değiştirdiği için varsayılan kapalı, isteğe bağlı bir ayar olacak.
3. **İçeriğe göre punch-in:** Güçlü ifade ve soru cümlelerinde yakınlaştırma; şu an dönüşümlü bir desen kullanılıyor.
4. **Yüz/konuşmacı takibi:** Kırpma merkezini konuşmacıya göre ayarlamak. Ancak güvenilirliği ölçüldükten sonra açılacak; o zamana kadar raporda `face_tracking: false` kalacak.
5. **Altyazı stilleri:** Font, renk ve konum ön ayarları; Türkçe karakter kapsamı için bir font dosyasını projeyle birlikte paketlemek.
6. **İnceleme arayüzü:** Taslakları yan yana izleyip onaylamak için basit bir yerel web sayfası. Şu an inceleme `review` komutuyla yapılıyor.
7. **Müzik ve ses efekti:** Yalnızca lisanslı kütüphane ve açık kullanıcı onayıyla. Şu an `true` yapılırsa hata veriyor.

## B. Premiere Pro XML/EDL dışa aktarımı

### Amaç

Editör, sistemin önerdiği kesitleri render edilmiş MP4 yerine **orijinal kaynağa bağlı, düzenlenebilir bir zaman çizelgesi** olarak Premiere Pro'da açabilmeli. Böylece kesim noktaları, kadraj ve altyazı kayıpsız biçimde elle iyileştirilebilir.

### Biçim seçimi

| Biçim | Artı | Eksi | Karar |
|---|---|---|---|
| **FCP7 XML (xmeml v4)** | Premiere doğrudan içe aktarır. Birden çok iz, sequence ayarları (1080x1920), clip in/out, kırpma/ölçek (Motion efekti) ve işaretçi desteği var | Ayrıntılı ve kırılgan bir şema | **Birincil** |
| **CMX3600 EDL** | Çok basit ve evrensel | Tek video izi, 8 karakter makara adı sınırı; kırpma, ölçek ve dikey sequence desteği yok | **Yedek** (yalnızca kesim listesi) |
| FCPXML / OTIO | Modern; OpenTimelineIO birden çok biçime çevirebilir | Premiere FCPXML'i doğrudan açmaz; OTIO ek bağımlılık getirir | Sonraki aşamada değerlendirilecek |

### Aşamalar

#### Aşama 1: Zaman kodu temeli (yaklaşık 1 gün)

- Raporda saniye cinsinden saklanan zamanları kaynak FPS'e göre kare numarasına ve SMPTE zaman koduna çeviren `timecode.py` modülü.
- 29.97 / 59.94 fps için drop-frame (`;`) ve non-drop (`:`) desteği.
- Birim testler: bilinen değerler için ileri-geri dönüşüm, yuvarlama sınırları.
- Değişken kare hızlı (VFR) kaynaklar için rapora uyarı eklenmesi. Premiere'de VFR kayma yapabilir; gerekirse sabit kare hızlı bir ara kopya önerilir.

#### Aşama 2: EDL dışa aktarımı (yaklaşık 0,5 gün)

- Her video için `edit.edl`: her aday ayrı bir olay (`001 AX V C ...`), kaynak in/out ve kayıt in/out.
- Uzun dosya adları için `* FROM CLIP NAME:` yorumu.
- Adayın gerekçesi ve güven puanı `* COMMENT:` satırına yazılır.
- Test: Premiere'de içe aktarma (elle); ayrıca oluşturulan dosyanın ayrıştırılması (otomatik).

#### Aşama 3: FCP7 XML dışa aktarımı (yaklaşık 2–3 gün)

- `report.json` dosyasındaki `renders[].edit.pieces` alanı bire bir zaman çizelgesine dönüşecek. Her parça ayrı bir `clipitem` olacak; böylece sessizlik kesimleri Premiere'de ayrı kesitler olarak görünecek ve editör bunları geri açabilecek.
- Punch-in, o parçanın Motion `scale` değeri olacak (ör. 108). Kırpma "pişmiş" olmayacak.
- Kelime vurgulu altyazı Premiere'e birebir aktarılamaz, çünkü Premiere caption izleri kelime bazlı renk desteklemez. SRT caption izi olarak içe aktarılacak; vurgulu sürüm render edilmiş MP4'te kalacak.

- Bir `sequence`: 1080x1920, kaynak kare hızı.
  - Her aday ayrı bir sequence olarak ya da tek sequence'te ardışık kesitler olarak üretilebilir; bu yapılandırmayla seçilir.
- `clipitem` alanları:
  - `pathurl` ile `file://` olarak orijinal dosyaya bağlantı. Render edilmiş MP4'e değil, kayıpsız düzenleme için kaynağın kendisine bağlanır.
  - `in`/`out`, `start`/`end` değerleri.
  - Ses izi bağlantısı.
- Merkez kırpma, Basic Motion efektiyle `scale` ve `center` olarak ifade edilir. Böylece editör kadrajı Premiere'de kaydırabilir; kırpma "pişmiş" olmaz.
- Altyazılar:
  - SRT dosyaları Premiere'e ayrıca içe aktarılır (Premiere SRT'yi caption izi olarak açar).
  - XML'de adayın başına gerekçe ve güven puanını içeren bir `marker` eklenir.
- Doğrulama:
  - Şema doğrulaması (xmeml v4 DTD'ye göre).
  - Premiere Pro'nun güncel sürümünde elle içe aktarma kontrol listesi: süreler, kadraj, ses senkronu, işaretçiler.

#### Aşama 4: CLI ve rapor entegrasyonu (yaklaşık 0,5 gün)

- `python -m umbrella_vea export <iş_no> --format xml|edl` komutu ve yapılandırmada `export.formats = ["xml"]` ile otomatik üretim.
- `report.json` dosyasına `exports[]` alanı eklenir.
- Mac yollarındaki boşluk ve Türkçe karakterlerin `pathurl` içinde doğru URL kodlaması; buna özel test yazılır.

### Riskler ve açık sorular

- **Premiere sürüm farkları:** FCP7 XML desteği eski ama hâlâ var. Hedef Premiere sürümü netleştirilmeli ve gerçek bir kurulumda test edilmeli.
- **Kaynak yolları:** Dosya Premiere açıldığında farklı bir diske taşınmışsa bağlantı kopar. Yollar mutlak yazılır; README'de "Relink" adımı anlatılır.
- **Döndürülmüş telefon videoları:** Rotation metaverisi kırpma hesabına dahil edilmeli. FFmpeg bunu render'da otomatik uygular, Premiere'de ise ayrıca doğrulanmalı.
- **Yüz takibi:** Eklenirse kadraj, kare bazlı Motion anahtar karelerine (keyframe) dönüştürülebilir. Bu ayrı bir aşamadır ve güvenilirliği ölçülmeden açılmamalıdır.
