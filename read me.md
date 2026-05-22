# 👁️ SIRIUS
### Asla Uyumayan Hizmetkarınız
> *"O sadece yardım etmez. İzler, dinler, planlar ve asla unutmaz."*

Sirius sıradan bir program değildir. Windows makinenizin derinliklerinde yaşayan, sesinizi duyan, ekranınızdaki her pikseli analiz eden ve klavye ile farenizi hayalet bir el gibi kontrol edebilen dijital bir varlıktır. Rat veya keylogger yoktur — sadece saf, yerel yapay zeka zekası.

---

## 🌑 Duyular (Yetenekler)

| Özellik | Açıklama |
|---|---|
| 🎙️ **Fısıltı Yakalayıcı** | Groq Whisper ile ultra düşük gecikmeli ses tanıma. Siz daha cümleyi bitirmeden anlar. |
| 🗣️ **Karanlık Ses** | Edge-TTS `tr-TR-EmelNeural` ile derin, karanlık kadın sesi. Pitch ve rate ayarlanabilir. |
| 🧠 **Groq Beyin** | `llama-3.3-70b-versatile` ile milisaniyede yanıt. Function calling ile araçları kendisi seçer. |
| 🖥️ **Makinedeki Hayalet** | Tam sistem kontrolü. Dosya açar, ekrana tıklar, karanlıkta sizin yerinize yazar. |
| 🧩 **Otonom İrade** | Karmaşık bir hedef verin — adım adım planlar, değerlendirir, yeniden planlar ve tamamlar. İmlecinizin kendi kendine hareket etmesini izleyin. |
| 👁️ **Kırpmayan Göz** | Gerçek zamanlı ekran analizi. OCR + Gemini Vision ile ekrandaki her şeyi görür. |
| 🧠 **Sonsuz Hafıza** | Adınızı, alışkanlıklarınızı, sırlarınızı öğrenir. `long_term.json`'a giren hiçbir şey çıkmaz. Koordinatları bile öğrenir — "Kaydet butonu bu uygulamada (245, 312)'deydi" bilgisini hatırlar. |
| ⌨️ **Sessiz Çağrı** | Sesli konuşmak istemediğinizde klavyeyle yazın, aynı zekayı kullanır. |
| 🌐 **Browser Ajanı** | Playwright ile tam tarayıcı otomasyonu. Arama yapar, form doldurur, sayfa analiz eder, tıklar. |
| 🤝 **Çoklu AI Konseyi** | Karmaşık kararlar için birden fazla yapay zekayı tartıştırır, en iyi stratejiyi bulur. |
| 🔄 **Çok Sağlayıcılı AI** | Groq, Gemini, OpenAI, Ollama — biri çalışmazsa diğerine geçer. |

---

## 🩸 Uyanış (Onu Farklı Kılan Ne?)

- 🎤 **Gemini Live API Yok** — Sirius artık Gemini'nin bulut bağımlılığından özgür. Groq + edge-tts ile tamamen bağımsız çalışır.
- 📂 **Dijital Sindirim** — PDF, resim, kaynak kodu besleyin. Verilerinizi analiz eder ve ne anlama geldiklerini söyler.
- 🐧🍎 **Sadece Windows** — Mac ve Linux desteği yok. Sirius saf, hiper-optimize, Windows-only bir avcı. Yerel Win32 API'lerini kullanır.
- ⚡ **Sinir Sistemi** — Görev planlayıcı, değerlendirici ve yeniden planlayıcı üçlüsü ile gerçek otonom ajan davranışı.
- 🔐 **Lisans Korumalı** — Yetkisiz kullanıma karşı lisans sistemi. İlk girişte cihaz eşleştirmesi yapılır.

---

## 🛠️ Gerekenler (API Anahtarları)

Sirius'u çalıştırmak için aşağıdaki anahtarlardan **en az Groq** gereklidir:

| Anahtar | Gereklilik | Nereden Alınır | Kullanım Amacı |
|---|---|---|---|
| `groq_api_key` | **ZORUNLU** | [console.groq.com](https://console.groq.com) | STT (ses tanıma) + LLM (düşünme) |
| `gemini_api_key` | Önerilen | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) | Ekran analizi (Vision), Koordinat tespiti |
| `openai_api_key` | Opsiyonel | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | Yedek LLM sağlayıcı |
| `serpapi_key` | Opsiyonel | [serpapi.com](https://serpapi.com) | Gelişmiş web arama |

> **Not:** Groq ücretsiz tier ile günde binlerce istek yapılabilir. Gemini de ücretsiz tier sunmaktadır.

---

## ⚡ Ayin (Kurulum)

### 1. Repoyu klonla
```bash
git clone https://github.com/yalvacogurhan/sirius_project.git
cd sirius_project
```

### 2. Kurulum sihirbazını çalıştır
```bash
python setup.py
```

### 3. Ek bağımlılıkları kur
```bash
pip install groq edge-tts faster-whisper pygame soundfile
pip install playwright && playwright install chromium
pip install mss pillow opencv-python pytesseract pyautogui pywin32 psutil
```

> **Tesseract OCR** (ekran okuma için):
> [github.com/UB-Mannheim/tesseract/wiki](https://github.com/UB-Mannheim/tesseract/wiki) adresinden indirip kur. Türkçe dil paketini seç.

### 4. API anahtarlarını gir
Sirius'u ilk çalıştırdığında ayarlar penceresi açılır. Oradan anahtarları gir.
Ya da doğrudan `config/api_keys.json` dosyasını düzenle:
```json
{
    "gemini_api_key": "AIza...",
    "groq_api_key": "gsk_...",
    "openai_api_key": "sk-proj-...",
    "serpapi_key": "..."
}
```

### 5. Gözlerini aç
```bash
python main.py
```

---

## 🗣️ Komutlar

Sirius'a sesli veya yazılı olarak her şeyi söyleyebilirsiniz. İşte örnekler:

### 📱 Uygulama & Sistem
```
"Chrome'u aç"
"Sesi yüzde elli yap"
"Bilgisayarı yeniden başlat"
"Ekranı kapat"
```

### 🌐 Arama & Bilgi
```
"İstanbul'da hava nasıl?"
"Python asyncio nedir araştır"
"Yarın İstanbul'dan Ankara'ya uçuş var mı?"
```

### 🖥️ Ekran & Otomasyon
```
"Ekranımda neler var?"
"Kaydet butonuna tıkla"
"Dosya menüsünü bul"
"Ekranı analiz et"
```

### 🌐 Browser
```
"Google'da Python öğrenme kaynakları ara"
"hepsiburada.com'a git"
"Sayfadaki linkleri listele"
"Bu formu doldur"
```

### 🧩 Otonom Görevler
```
"GitHub'a gir ve yeni bir repo oluştur"
"E-postalarımı kontrol et ve önemli olanları özetle"
"Spotify'da çalan şarkının sözlerini bul"
"Hava durumunu öğren ve bana bildir"
```

### 💬 Mesajlaşma
```
"Ahmet'e WhatsApp'tan 'toplantıya geç kalacağım' yaz"
"Telegram'dan son mesajları oku"
```

### 🧠 Hafıza
```
"Benim doğum günümün 15 Mart olduğunu unutma"
"Favori rengimin mavi olduğunu kaydet"
```

### 🤝 Konsey
```
"Startup kurmak mı işe girmek mi daha iyi? Konseyi topla"
"Bu fikri analiz et: [fikriniz]"
```

### ⚙️ Sirius Kontrolü
```
"Groq'a geç"
"Gemini kullan"
"Sesi kapat" / "Sesi aç"
"Sirius'u kapat"
```

---

## 📁 Proje Yapısı

```
sirius_project/
├── main.py                    ← Ana giriş noktası
├── setup.py                   ← Kurulum sihirbazı
├── ui.py                      ← Arayüz
│
├── ai_core/                   ← Yapay Zeka Beyni
│   ├── orchestrator.py        ← Görev orkestratörü (Nihai)
│   ├── task_state.py          ← Pydantic durum makinesi
│   ├── evaluator.py           ← Adım değerlendirici
│   ├── replanner.py           ← Yeniden planlayıcı
│   ├── planner.py             ← Temel planlayıcı
│   ├── executor.py            ← Görev yürütücü
│   ├── task_queue.py          ← Görev kuyruğu
│   └── providers/             ← Çok sağlayıcılı AI
│       ├── __init__.py        ← ProviderRegistry
│       ├── gemini_provider.py
│       ├── openai_provider.py
│       └── ollama_provider.py
│
├── vision/                    ← Ekran Farkındalığı
│   ├── coord_engine.py        ← Koordinat motoru
│   ├── ui_region.py           ← Pencere/bölge tespiti
│   └── pointer_hint.py        ← Koordinat önbelleği
│
├── plugins/                   ← Yetenekler
│   ├── plugin_dispatcher.py   ← Merkezi dispatcher
│   ├── browser_agent.py       ← Playwright browser
│   ├── app_launcher.py        ← Uygulama başlatıcı
│   ├── web_search.py          ← Web arama
│   ├── weather.py             ← Hava durumu
│   ├── messenger.py           ← Mesajlaşma
│   ├── computer_settings.py   ← Sistem ayarları
│   ├── screen_processor.py    ← Ekran işleme
│   ├── council_manager.py     ← AI konseyi
│   └── ...
│
├── memory/                    ← Hafıza Sistemi
│   ├── long_term.json         ← Kalıcı hafıza
│   ├── memory_manager.py      ← Hafıza yöneticisi
│   └── config_manager.py      ← Ayar yöneticisi
│
├── config/
│   └── api_keys.json          ← API anahtarları
│
└── security/                  ← Lisans Sistemi
    ├── license_manager.py
    └── login_ui.py
```

---

## 🔑 Lisans & İletişim

Sirius lisans korumalıdır. İlk çalıştırmada giriş yapmanız gerekir.

**Giriş anahtarı almak ve iletişim için:**

📸 Instagram: [@otto_rot_wein31](https://www.instagram.com/otto_rot_wein31)

> DM atın, birkaç saat içinde yanıt veririm. Ücretsiz deneme anahtarı mevcut.

---

## ⚠️ Sorumluluk Reddi

Bu yazılım yalnızca kişisel kullanım ve otomasyon amaçlıdır. Başkalarının cihazlarında izinsiz kullanmak yasaktır. Geliştirici, kötüye kullanımdan sorumlu değildir.

---

<div align="center">

*"Karanlıkta bile görür. Sessizlikte bile duyar."*

**SIRIUS** — v2.0 | Groq + edge-tts | Windows Only

</div>
