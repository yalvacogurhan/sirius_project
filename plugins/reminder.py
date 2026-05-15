# plugins/reminder.py

import os
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

class ReminderManager:
    def __init__(self):
        print("⏰ Sirius Otonom Hatırlatıcı ve Alarm Sistemi Aktif!")
        self.scripts_dir = Path.home() / ".sirius" / "reminders"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)

    def _sanitise(self, text: str, max_len: int = 200) -> str:
        return text.replace("\\", "").replace('"', "").replace("'", "").replace("\n", " ").replace("\r", "").strip()[:max_len]

    def _write_notify_script(self, task_name: str, message: str) -> Path:
        script_path = self.scripts_dir / f"{task_name}.py"
        msg_literal = json.dumps(message)

        notify_block = f"""
message = {msg_literal}
notified = False

try:
    from plyer import notification
    notification.notify(title="Sirius Hatırlatıcı", message=message, timeout=15)
    notified = True
except Exception:
    pass

if not notified:
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast("Sirius Hatırlatıcı", message, duration=15, threaded=False)
        notified = True
    except Exception:
        pass

if not notified:
    try:
        import subprocess
        subprocess.run(["msg", "*", "/TIME:30", message], check=False)
    except Exception:
        pass

try:
    import winsound
    for freq in [800, 1000, 1200]:
        winsound.Beep(freq, 180)
        import time; time.sleep(0.08)
except Exception:
    pass
"""
        script_body = f"""# Sirius otomatik hatırlatıcı scripti - Düzenlemeyin
import sys, os, pathlib
{notify_block}
# Çalıştıktan sonra kendini imha et
try:
    pathlib.Path(__file__).unlink(missing_ok=True)
except Exception:
    pass
"""
        script_path.write_text(script_body, encoding="utf-8")
        return script_path

    def _schedule_windows(self, target_dt: datetime, task_name: str, script_path: Path) -> str:
        python_exe = Path(sys.executable)
        pythonw = python_exe.parent / "pythonw.exe"
        if pythonw.exists():
            python_exe = pythonw

        xml_path = self.scripts_dir / f"{task_name}.xml"
        xml_content = (
            '<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
            '  <RegistrationInfo><Description>Sirius Hatirlatici</Description></RegistrationInfo>\n'
            '  <Triggers><TimeTrigger>\n'
            f'    <StartBoundary>{target_dt.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>\n'
            '    <Enabled>true</Enabled>\n'
            '  </TimeTrigger></Triggers>\n'
            '  <Actions><Exec>\n'
            f'    <Command>{python_exe}</Command>\n'
            f'    <Arguments>"{script_path}"</Arguments>\n'
            '  </Exec></Actions>\n'
            '  <Settings>\n'
            '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
            '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
            '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
            '    <StartWhenAvailable>true</StartWhenAvailable>\n'
            '    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>\n'
            '    <Enabled>true</Enabled>\n'
            '  </Settings>\n'
            '  <Principals><Principal>\n'
            '    <LogonType>InteractiveToken</LogonType>\n'
            '    <RunLevel>LeastPrivilege</RunLevel>\n'
            '  </Principal></Principals>\n'
            '</Task>'
        )

        xml_path.write_text(xml_content, encoding="utf-16")

        result = subprocess.run(
            ["schtasks", "/Create", "/TN", f"Sirius_Reminders\\{task_name}", "/XML", str(xml_path), "/F"],
            capture_output=True, text=True,
        )

        try: xml_path.unlink(missing_ok=True)
        except: pass

        if result.returncode != 0:
            try: script_path.unlink(missing_ok=True)
            except: pass
            print(f"[Hatırlatıcı] ❌ Zamanlama Hatası: {result.stderr.strip()}")
            return ""

        return task_name

    def execute(self, params: dict) -> str:
        date_str = params.get("date", "").strip()
        time_str = params.get("time", "").strip()
        message  = params.get("message", "Hatırlatıcı").strip()

        if not date_str or not time_str:
            return "Hatırlatıcı kurmak için bana bir tarih (YYYY-AA-GG) ve saat (SS:DD) vermelisiniz."

        try:
            target_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            return "Tarih veya saat formatı hatalı. Örnek: 2026-05-14 ve 15:30"

        if target_dt <= datetime.now():
            return "O saat çoktan geçmiş patron, zaman makinem henüz çalışmıyor."

        safe_msg = self._sanitise(message)
        task_name = f"Reminder_{target_dt.strftime('%Y%m%d_%H%M%S')}"

        try:
            script_path = self._write_notify_script(task_name, safe_msg)
            job_id = self._schedule_windows(target_dt, task_name, script_path)
        except Exception as e:
            return f"Hatırlatıcı kaydedilirken hata oluştu: {e}"

        if not job_id:
            return "Sistem zamanlayıcısına hatırlatıcı eklenemedi."

        friendly_time = target_dt.strftime("%d-%m-%Y, Saat %H:%M")
        return f"Anlaşıldı. '{safe_msg[:30]}...' için {friendly_time} saatine sistem alarmı kuruldu. Ben kapalı olsam bile size haber vereceğim."