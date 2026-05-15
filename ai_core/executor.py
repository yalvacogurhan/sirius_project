# ai_core/executor.py

import json
import re
import sys
import threading
import subprocess
import tempfile
import os
from pathlib import Path

from ai_core.error_handler import ErrorDecision

class SiriusExecutor:
    def __init__(self, brain, error_handler, plugins_dict):
        print("⚙️ Sirius Otonom Görev Yürütücü (Executor) Aktif!")
        self.gemini_key = ""
        self.brain = brain
        self.error_handler = error_handler
        self.plugins = plugins_dict  # main.py'den gelecek aktif modüller sözlüğü
        self.MAX_REPLAN_ATTEMPTS = 2

    def _run_generated_code(self, description: str, speak=None) -> str:
        try:
            from google import genai
            client = genai.Client(api_key=self.gemini_key)
        except ImportError:
            return "google-genai kütüphanesi eksik."

        if speak:
            speak("Bu görev için özel bir kod yazıp çalıştırıyorum patron.")

        home = Path.home()
        desktop = home / "Desktop"
        downloads = home / "Downloads"
        documents = home / "Documents"

        prompt = (
            "Sen uzman bir Python geliştiricisisin. "
            "İstenen görevi yerine getirecek temiz, eksiksiz ve çalışan bir Python kodu yaz. "
            "SADECE Python kodunu döndür. Açıklama veya markdown (```) kullanma.\n\n"
            f"SİSTEM YOLLARI:\n"
            f"  Desktop   = r'{desktop}'\n"
            f"  Downloads = r'{downloads}'\n"
            f"  Documents = r'{documents}'\n"
            f"  Home      = r'{home}'\n\n"
            f"Görev: {description}"
        )

        try:
            response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            
            # Arayüz bug'ı kalkanı
            marker = chr(96) * 3
            code = response.text.replace(marker + "python", "").replace(marker, "").strip()

            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
                f.write(code)
                tmp_path = f.name

            print(f"[Executor] 🐍 Üretilen kod çalıştırılıyor: {tmp_path}")

            result = subprocess.run([sys.executable, tmp_path], capture_output=True, text=True, timeout=120, cwd=str(home))

            try: os.unlink(tmp_path)
            except: pass

            output = result.stdout.strip()
            error = result.stderr.strip()

            if result.returncode == 0 and output: return output
            elif result.returncode == 0: return "Görev başarıyla tamamlandı."
            elif error: raise RuntimeError(f"Kod hatası: {error[:400]}")
            return "Tamamlandı."

        except subprocess.TimeoutExpired:
            raise RuntimeError("Üretilen kod 120 saniyede zaman aşımına uğradı.")
        except Exception as e:
            raise RuntimeError(f"Kod üretimi veya çalıştırılması başarısız: {e}")

    def _inject_context(self, params: dict, tool: str, step_results: dict, goal: str = "") -> dict:
        if not step_results: return params
        params = dict(params)

        if tool == "file_controller" and params.get("action") in ("write", "create_file"):
            content = params.get("content", "")
            if not content or len(content) < 50:
                all_results = [v for v in step_results.values() if v and len(v) > 100 and v not in ("Done.", "Completed.", "Tamamlandı.")]
                if all_results:
                    combined = "\n\n---\n\n".join(all_results)
                    params["content"] = combined # Çeviri yükünü hafifletmek için doğrudan bağlandı
                    print(f"[Executor] 💉 İçerik bağlamı enjekte edildi")
        return params

    def _call_tool(self, tool: str, parameters: dict, speak) -> str:
        # 1. Modül sistemimizde kayıtlıysa (plugins_dict içindeyse)
        if tool in self.plugins:
            plugin = self.plugins[tool]
            # Standart 'execute' fonksiyonuna sahip eklentiler
            if hasattr(plugin, "execute"):
                return str(plugin.execute(parameters))
            # İstisnai fonksiyon isimleri (örn: messenger.send_message)
            elif tool == "messenger" and hasattr(plugin, "send_message"):
                return str(plugin.send_message(parameters.get("platform"), parameters.get("receiver"), parameters.get("message")))
            elif tool == "windows" and hasattr(plugin, "manage_window"):
                return str(plugin.manage_window(parameters.get("target_name"), parameters.get("command")))

        # 2. Özel kod üretimi veya bilinmeyen araç fallback'i
        elif tool == "generated_code":
            desc = parameters.get("description", "")
            if not desc: raise ValueError("generated_code için 'description' parametresi eksik.")
            return self._run_generated_code(desc, speak=speak)

        print(f"[Executor] ⚠️ Bilinmeyen araç '{tool}' — özel kod üretimine dönülüyor")
        return self._run_generated_code(f"Şu görevi yap: {parameters}", speak=speak)

    def execute_plan(self, goal: str, plan_data: dict, speak=None, cancel_flag: threading.Event = None) -> str:
        print(f"\n[Executor] 🎯 Hedef: {goal}")

        replan_attempts = 0
        completed_steps = []
        step_results = {} 
        plan = plan_data

        while True:
            steps = plan.get("steps", [])
            if not steps:
                msg = "Bu görev için geçerli bir plan oluşturamadım patron."
                if speak: speak(msg)
                return msg

            success = True
            failed_step = None
            failed_error = ""

            for step in steps:
                if cancel_flag and cancel_flag.is_set():
                    if speak: speak("Görev iptal edildi patron.")
                    return "Görev iptal edildi."

                step_num = step.get("step", "?")
                tool = step.get("tool", "generated_code")
                desc = step.get("description", "")
                params = step.get("parameters", {})

                params = self._inject_context(params, tool, step_results, goal=goal)
                print(f"\n[Executor] ▶️ Adım {step_num}: [{tool}] {desc}")

                attempt = 1
                step_ok = False

                while attempt <= 3:
                    if cancel_flag and cancel_flag.is_set(): break
                    try:
                        result = self._call_tool(tool, params, speak)
                        step_results[step_num] = result 
                        completed_steps.append(step)
                        print(f"[Executor] ✅ Adım {step_num} bitti: {str(result)[:100]}")
                        step_ok = True
                        break

                    except Exception as e:
                        error_msg = str(e)
                        print(f"[Executor] ❌ Adım {step_num} (Deneme {attempt}) başarısız: {error_msg}")

                        recovery = self.error_handler.analyze_error(step, error_msg, attempt=attempt)
                        decision = recovery["decision"]
                        user_msg = recovery.get("user_message", "")

                        if speak and user_msg: speak(user_msg)

                        if decision == ErrorDecision.RETRY:
                            attempt += 1
                            import time; time.sleep(2)
                            continue

                        elif decision == ErrorDecision.SKIP:
                            print(f"[Executor] ⏭️ Adım {step_num} atlanıyor")
                            completed_steps.append(step)
                            step_ok = True
                            break

                        elif decision == ErrorDecision.ABORT:
                            msg = f"Görev iptal edildi patron. {recovery.get('reason', '')}"
                            if speak: speak(msg)
                            return msg

                        else: # REPLAN (Farklı çözüm üret)
                            fix_suggestion = recovery.get("fix_suggestion", "")
                            if fix_suggestion and tool != "generated_code":
                                try:
                                    fixed_step = self.error_handler.generate_fix(step, error_msg, fix_suggestion)
                                    if speak: speak("Alternatif bir yaklaşım deniyorum patron.")
                                    res = self._call_tool(fixed_step["tool"], fixed_step["parameters"], speak)
                                    step_results[step_num] = res
                                    completed_steps.append(step)
                                    step_ok = True
                                    break
                                except Exception as fix_err:
                                    print(f"[Executor] ⚠️ Alternatif çözüm başarısız: {fix_err}")

                    failed_step = step
                    failed_error = error_msg
                    success = False
                    break

                if not step_ok and not failed_step:
                    failed_step = step
                    failed_error = "Maksimum deneme sayısı aşıldı"
                    success = False

                if not success: break

            if success:
                return self._summarize(goal, completed_steps, speak)

            if replan_attempts >= self.MAX_REPLAN_ATTEMPTS:
                msg = f"{replan_attempts} yeniden planlama denemesinden sonra görev başarısız oldu patron."
                if speak: speak(msg)
                return msg

            if speak: speak("Planımı güncelliyorum patron.")
            replan_attempts += 1
            
            # Not: Beyin modülü planı günceller (Mevcut main.py entegrasyonu için Planner'a bağlanmalı)
            plan = self.brain.get_action(f"Hedef başarısız oldu. Hata: {failed_error}. Yeni bir plan yap.") 

    def _summarize(self, goal: str, completed_steps: list, speak) -> str:
        fallback = f"Tamamdır patron. {len(completed_steps)} adımlık görev tamamlandı."
        try:
            from google import genai
            client = genai.Client(api_key=self.gemini_key)
            steps_str = "\n".join(f"- {s.get('description', '')}" for s in completed_steps)
            prompt = (
                f'Kullanıcı hedefi: "{goal}"\nTamamlanan adımlar:\n{steps_str}\n\n'
                "Nelerin başarıldığını özetleyen tek bir doğal cümle yaz. Kullanıcıya 'patron' diye hitap et."
            )
            response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            marker = chr(96) * 3
            summary = response.text.replace(marker, "").strip()
            if speak: speak(summary)
            return summary
        except Exception:
            if speak: speak(fallback)
            return fallback