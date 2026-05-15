# ai_core/planner.py

import json
from google import genai

class Planner:
    def __init__(self):
        print("🧠 Sirius Çok Adımlı Stratejik Planlama Motoru Aktif!")
        self.gemini_key = "" # main.py'den beslenecek
        self.system_prompt = (
            "Sen Sirius yapay zeka asistanının 'Planlama' modülüsün. "
            "Kullanıcının hedefini SADECE aşağıdaki araçları kullanarak adımlara böl. "
            "Her adım bağımsızdır. Maksimum 5 adım kullan.\n\n"
            "KULLANILABİLİR ARAÇLAR VE PARAMETRELERİ:\n"
            "- windows: Pencere kontrolü (command: close/minimize/maximize, target_name)\n"
            "- sys_set: Bilgisayar ayarı (command: volume_up/brightness_down vb.)\n"
            "- launcher: Uygulama açma (app_name)\n"
            "- web: İnternet araması (query)\n"
            "- messenger: Mesaj atma (platform, receiver, message)\n"
            "- coder: Kod işlemleri (code_action: write/run, description)\n"
            "- dev_agent: Proje geliştirme (description)\n"
            "- desktop: Masaüstü kontrolü (d_action: organize/clean, path)\n"
            "- file_ctrl: Dosya yönetimi (f_action: list/delete/move, path, name)\n"
            "- games: Oyun yönetimi (g_action: update/install, game_name)\n"
            "- remind: Hatırlatıcı kurma (date, time, message)\n"
            "- vision: Görme ve ekran okuma (angle: screen/camera, text)\n"
            "- travel: Seyahat arama (type: uçak/otobüs, origin, destination, date)\n"
            "- yt: YouTube işlemleri (yt_action: play/summarize, query, url)\n"
            "- weather: Hava durumu (city, time)\n\n"
            "Örnek Hedef: 'Hava durumuna bakıp John'a WhatsApp'tan at'\n"
            "Adım 1: tool: 'weather', parameters: {'city': 'İstanbul', 'time': 'bugün'}\n"
            "Adım 2: tool: 'messenger', parameters: {'platform': 'whatsapp', 'receiver': 'John', 'message': 'Hava çok güzel'}\n\n"
            "SADECE aşağıdaki formatta geçerli bir JSON döndür. Markdown veya açıklama KULLANMA:\n"
            "{\n"
            '  "goal": "hedef açıklaması",\n'
            '  "steps": [\n'
            '    {\n'
            '      "step": 1,\n'
            '      "tool": "arac_ismi",\n'
            '      "description": "bu adım ne yapıyor",\n'
            '      "parameters": {"parametre1": "değer1"},\n'
            '      "critical": true\n'
            '    }\n'
            '  ]\n'
            "}"
        )

    def get_action(self, goal: str, context: str = "") -> dict:
        """Kullanıcının isteğini çok adımlı bir plana dönüştürür."""
        if not self.gemini_key:
            return self._fallback_plan(goal)

        try:
            client = genai.Client(api_key=self.gemini_key)
            user_input = f"Hedef: {goal}"
            if context: user_input += f"\nBağlam: {context}"

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_input,
                config={"system_instruction": self.system_prompt}
            )

            # Arayüz Kopyalama Bug'ına karşı Kalkan (Markdown Temizleyici)
            marker = chr(96) * 3
            text = response.text.replace(marker + "json", "").replace(marker, "").strip()

            plan = json.loads(text)

            if "steps" not in plan or not isinstance(plan["steps"], list):
                raise ValueError("Geçersiz plan yapısı")

            print(f"[Planner] ✅ Plan Oluşturuldu: {len(plan['steps'])} adım")
            for s in plan["steps"]:
                print(f"  Adım {s['step']}: [{s['tool']}] {s['description']}")

            return plan

        except Exception as e:
            print(f"[Planner] ⚠️ Planlama başarısız oldu: {e}")
            return self._fallback_plan(goal)

    def replan(self, goal: str, completed_steps: list, failed_step: dict, error: str) -> dict:
        """Bir adım başarısız olursa, kalanlar için yeni bir strateji (B Planı) çizer."""
        try:
            client = genai.Client(api_key=self.gemini_key)
            
            completed_summary = "\n".join(f"  - Adım {s.get('step')} ({s.get('tool')}): TAMAMLANDI" for s in completed_steps)
            
            prompt = (
                f"Hedef: {goal}\n\nTamamlananlar:\n{completed_summary if completed_summary else '  (hiçbiri)'}\n\n"
                f"Başarısız adım: [{failed_step.get('tool')}] {failed_step.get('description')}\n"
                f"Hata: {error}\n\n"
                "SADECE kalan işler için REVİZE EDİLMİŞ yeni bir plan (JSON) oluştur. Tamamlanan adımları tekrar etme."
            )

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"system_instruction": self.system_prompt}
            )

            marker = chr(96) * 3
            text = response.text.replace(marker + "json", "").replace(marker, "").strip()
            plan = json.loads(text)

            print(f"[Planner] 🔄 Revize B Planı Devrede: {len(plan.get('steps', []))} adım")
            return plan

        except Exception as e:
            print(f"[Planner] ⚠️ Yeniden planlama başarısız: {e}")
            return self._fallback_plan(goal)

    def _fallback_plan(self, goal: str) -> dict:
        """Sistem çökerse devreye giren son çare, tek adımlık acil durum planı."""
        print("[Planner] 🔄 Acil durum planı (Fallback) devrede.")
        return {
            "goal": goal,
            "steps": [
                {
                    "step": 1,
                    "tool": "web",
                    "description": f"Şunu araştır: {goal}",
                    "parameters": {"query": goal},
                    "critical": True
                }
            ]
        }