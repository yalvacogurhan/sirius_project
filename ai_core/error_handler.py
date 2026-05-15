# ai_core/error_handler.py

import json
import re
from enum import Enum

class ErrorDecision(Enum):
    RETRY   = "retry"      
    SKIP    = "skip"       
    REPLAN  = "replan"     
    ABORT   = "abort"      

class ErrorHandler:
    def __init__(self):
        print("🛡️ Sirius Otonom Hata Çözümleme ve Kriz Yönetim Motoru Aktif!")
        self.gemini_key = "" # main.py'den gelecek
        self.analyst_prompt = (
            "Sen Sirius yapay zeka asistanının hata kurtarma (error recovery) modülüsün. "
            "Bir görev adımı başarısız oldu. Hatayı analiz et ve ne yapılacağına karar ver.\n\n"
            "KARARLAR:\n"
            "- retry   : Geçici hata (ağ zaman aşımı, dosya kilidi vb.). Tekrar denenirse başarılı olabilir.\n"
            "- skip    : Bu adım kritik değil ve görev onsuz da tamamlanabilir.\n"
            "- replan  : Yaklaşım yanlıştı. Farklı bir araç veya yöntem denenmeli.\n"
            "- abort   : Görev temelden imkansız veya devam etmesi güvensiz.\n\n"
            "Ayrıca şunları sağla:\n"
            "- Neden başarısız olduğuna dair kısa açıklama (1 cümle)\n"
            "- Karar 'replan' ise çözüm önerisi (yerine ne denenmeli)\n"
            "- Karar 'retry' ise maksimum deneme sayısı (1 veya 2)\n\n"
            "SADECE geçerli JSON döndür:\n"
            "{\n"
            '  "decision": "retry|skip|replan|abort",\n'
            '  "reason": "neden başarısız oldu",\n'
            '  "fix_suggestion": "ne denenmeli (replan için)",\n'
            '  "max_retries": 1,\n'
            '  "user_message": "Kullanıcıya söylenecek kısa mesaj (Maksimum 15 kelime, \'Patron\' hitabını kullan)"\n'
            "}"
        )

    def analyze_error(self, step: dict, error: str, attempt: int = 1, max_attempts: int = 2) -> dict:
        if attempt >= max_attempts:
            print(f"[HataYöneticisi] ⚠️ Adım {step.get('step')} için maksimum denemeye ulaşıldı — yeniden planlanıyor")
            return {
                "decision":      ErrorDecision.REPLAN,
                "reason":        f"{attempt} kez başarısız oldu: {error[:100]}",
                "fix_suggestion": "Tamamen farklı bir araç veya yaklaşım dene",
                "max_retries":   0,
                "user_message":  "Aynı hatayı alıyorum, tamamen farklı bir yaklaşım deniyorum patron."
            }

        try:
            from google import genai
            client = genai.Client(api_key=self.gemini_key)
            
            prompt = f"Başarısız adım:\nAraç: {step.get('tool')}\nAçıklama: {step.get('description')}\nParametreler: {json.dumps(step.get('parameters', {}), indent=2)}\nKritik: {step.get('critical', False)}\n\nHata:\n{error[:500]}\n\nDeneme sayısı: {attempt}"

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"system_instruction": self.analyst_prompt}
            )
            
            # SİHİRLİ TEMİZLİK SATIRI (Arayüz bug'ını aşmak için hacker yöntemi)
            marker = chr(96) * 3
            text = response.text.replace(marker + "json", "").replace(marker, "").strip()
            result = json.loads(text)
            
            decision_str = result.get("decision", "replan").lower()
            decision_map = {
                "retry":  ErrorDecision.RETRY,
                "skip":   ErrorDecision.SKIP,
                "replan": ErrorDecision.REPLAN,
                "abort":  ErrorDecision.ABORT,
            }
            result["decision"] = decision_map.get(decision_str, ErrorDecision.REPLAN)

            if step.get("critical") and result["decision"] == ErrorDecision.SKIP:
                result["decision"]     = ErrorDecision.REPLAN
                result["user_message"] = "Bu adım kritikti patron, atlayamadığım için alternatif bir yol arıyorum."

            print(f"[HataYöneticisi] Karar: {result['decision'].value} — {result.get('reason', '')}")
            return result

        except Exception as e:
            print(f"[HataYöneticisi] ⚠️ Analiz başarısız: {e} — varsayılan olarak yeniden planlanıyor (replan)")
            return {
                "decision":       ErrorDecision.REPLAN,
                "reason":         str(e),
                "fix_suggestion": "Alternatif yaklaşım dene",
                "max_retries":    1,
                "user_message":   "Bir pürüz çıktı, hemen B planına geçiyorum patron."
            }

    def generate_fix(self, step: dict, error: str, fix_suggestion: str) -> dict:
        try:
            from google import genai
            client = genai.Client(api_key=self.gemini_key)
            
            prompt = (
                f"Bir görev adımı başarısız oldu. Yedek bir adım oluştur.\n\n"
                f"Orijinal adım:\nAraç: {step.get('tool')}\nAçıklama: {step.get('description')}\n"
                f"Parametreler: {json.dumps(step.get('parameters', {}), indent=2)}\n\n"
                f"Hata: {error[:300]}\nÇözüm önerisi: {fix_suggestion}\n\n"
                f"Aynı amaca farklı bir yoldan ulaşan bir Python scripti yaz.\n"
                f"SADECE Python kodunu döndür, hiçbir açıklama ekleme."
            )

            response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            
            # İKİNCİ SİHİRLİ TEMİZLİK SATIRI (Alttaki gizli hatayı yok eden kısım)
            marker = chr(96) * 3
            code = response.text.replace(marker + "python", "").replace(marker, "").strip()

            return {
                "step":        step.get("step"),
                "tool":        "code_helper",
                "description": f"Otomatik Düzeltme: {step.get('description')}",
                "parameters": {
                    "action":      "run",
                    "description": fix_suggestion,
                    "code":        code,
                    "language":    "python"
                },
                "depends_on": step.get("depends_on", []),
                "critical":   step.get("critical", False)
            }

        except Exception as e:
            print(f"[HataYöneticisi] ⚠️ Çözüm (Fix) üretilemedi: {e}")
            return {
                "step":        step.get("step"),
                "tool":        "code_helper",
                "description": f"Yedek Adım: {step.get('description')}",
                "parameters":  {"action": "write", "description": step.get("description", "")},
                "depends_on":  step.get("depends_on", []),
                "critical":    step.get("critical", False)
            }