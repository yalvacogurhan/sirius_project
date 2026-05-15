# plugins/dev_agent.py

import subprocess
import sys
import json
import re
import time
from pathlib import Path

class RateLimitError(Exception):
    pass

class DevAgent:
    def __init__(self):
        print("👨‍💻 Sirius Otonom Yazılım Geliştirme Ekibi (DevAgent) Aktif!")
        self.gemini_key = "" # main.py'den yüklenecek
        self.projects_dir = Path.home() / "Desktop" / "SiriusProjects"
        self.max_fix_attempts = 5
        self.model_name = "gemini-2.5-flash"

    def _get_model(self):
        import google.generativeai as genai
        if not self.gemini_key:
            raise ValueError("Gemini API anahtarı eksik!")
        genai.configure(api_key=self.gemini_key)
        return genai.GenerativeModel(self.model_name)

    def _strip_fences(self, text: str) -> str:
        text = text.strip()
        text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
        text = re.sub(r"\r?\n?```\s*$", "", text)
        return text.strip()

    def _is_rate_limit(self, error: Exception) -> bool:
        msg = str(error).lower()
        return "429" in msg or "quota" in msg or "resource_exhausted" in msg

    def _parse_traceback(self, output: str, project_files: list[str]) -> tuple[str | None, int | None]:
        pattern = re.compile(r'File ["\']([^"\']+\.py)["\'],\s+line\s+(\d+)', re.IGNORECASE)
        matches = pattern.findall(output)
        for raw_path, line_str in reversed(matches):
            raw_name = Path(raw_path).name
            for pf in project_files:
                if Path(pf).name == raw_name or pf == raw_path or raw_path.endswith(pf):
                    return pf, int(line_str)
        return None, None

    def _classify_error(self, output: str) -> str:
        low = output.lower()
        if any(x in low for x in ("no module named", "modulenotfounderror", "importerror")): return "dependency_error"
        if "syntaxerror" in low or "invalid syntax" in low: return "syntax_error"
        if "cannot import" in low or "importerror" in low: return "import_error"
        if any(x in low for x in ("traceback", "exception", "error:", "nameerror", "typeerror", "attributeerror", "valueerror", "keyerror", "indexerror", "zerodivisionerror", "filenotfounderror", "permissionerror")):
            return "runtime_error"
        return "none"

    def _has_error(self, output: str, run_command: str) -> bool:
        low = output.lower()
        if "timed out" in low or not output.strip(): return False
        return self._classify_error(output) != "none"

    def _plan_project(self, description: str, language: str) -> dict:
        model = self._get_model()
        prompt = f"""You are a senior software architect. Create a minimal, complete file plan for this project.
Language: {language}
Description: {description}

Return ONLY valid JSON (No markdown, no explanation):
{{
  "project_name": "snake_case_name",
  "entry_point": "main.py",
  "files": [
    {{"path": "main.py", "description": "Entry point...", "imports": ["utils.helpers"]}},
    {{"path": "utils/helpers.py", "description": "Helper utilities", "imports": []}}
  ],
  "run_command": "python main.py",
  "dependencies": ["requests"]
}}
Rules: Dependency order (files with no imports first). Minimal files. Standard libs (os, sys) are NOT dependencies."""
        try:
            resp = model.generate_content(prompt)
            return json.loads(self._strip_fences(resp.text))
        except json.JSONDecodeError as e:
            raise ValueError(f"Planlama geçersiz JSON döndürdü: {e}")
        except Exception as e:
            if self._is_rate_limit(e): raise RateLimitError(str(e))
            raise

    def _write_file(self, file_info: dict, proj_desc: str, all_files: list[dict], lang: str, proj_dir: Path, already_written: dict[str, str]) -> str:
        model = self._get_model()
        file_path = file_info["path"]
        file_desc = file_info.get("description", "")
        file_imports = file_info.get("imports", [])

        file_list = "\n".join(f"  - {f['path']}" for f in all_files)
        dep_ctx = "".join(f"\n--- {dep.replace('.', '/')}.py ---\n{already_written.get(dep.replace('.', '/') + '.py', '')[:1000]}" for dep in file_imports)

        prompt = f"""You are a senior {lang} developer. Project: {proj_desc}
All files: {file_list}
{dep_ctx}
Write the complete, working code for: {file_path} (Purpose: {file_desc})
Rules: Output ONLY raw code (no markdown). COMPLETE code (no stubs). Use proper type hints and error handling. Match import paths exactly."""
        try:
            resp = model.generate_content(prompt)
            code = self._strip_fences(resp.text)
            full_path = proj_dir / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(code, encoding="utf-8")
            print(f"[DevAgent] ✅ Yazıldı: {file_path}")
            return code
        except Exception as e:
            if self._is_rate_limit(e): raise RateLimitError(str(e))
            raise

    def _install_dependencies(self, dependencies: list[str], proj_dir: Path) -> str:
        if not dependencies: return "Harici kütüphane gerekmiyor."
        to_install = []
        for dep in dependencies:
            pkg = re.split(r"[>=<!]", dep)[0].strip()
            if subprocess.run([sys.executable, "-m", "pip", "show", pkg], capture_output=True).returncode != 0:
                to_install.append(dep)
        
        if not to_install: return f"Tüm kütüphaneler zaten kurulu: {', '.join(dependencies)}"
        print(f"[DevAgent] 📦 Kuruluyor: {to_install}")
        res = subprocess.run([sys.executable, "-m", "pip", "install"] + to_install, capture_output=True, text=True, cwd=str(proj_dir))
        return f"Kütüphaneler kuruldu: {', '.join(to_install)}" if res.returncode == 0 else f"Kurulum uyarısı: {res.stderr[:100]}"

    def _open_vscode(self, proj_dir: Path):
        for cmd in ["code", rf"C:\Users\{Path.home().name}\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd", r"C:\Program Files\Microsoft VS Code\bin\code.cmd"]:
            try:
                subprocess.Popen([cmd, str(proj_dir)], shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"[DevAgent] 💻 VSCode açıldı: {proj_dir}")
                return
            except: continue

    def _run_project(self, run_cmd: str, proj_dir: Path, timeout: int) -> str:
        print(f"[DevAgent] 🚀 Çalıştırılıyor: {run_cmd}")
        try:
            parts = run_cmd.split()
            if parts[0].lower() == "python": parts[0] = sys.executable
            res = subprocess.run(parts, capture_output=True, text=True, timeout=timeout, cwd=str(proj_dir))
            out, err = res.stdout.strip(), res.stderr.strip()
            parts_out = []
            if out: parts_out.append(f"ÇIKTI:\n{out}")
            if err: parts_out.append(f"HATA:\n{err}")
            return "\n\n".join(parts_out) if parts_out else "Çıktı vermeden çalıştı."
        except subprocess.TimeoutExpired: return "Zaman aşımı (Muhtemelen sunucu veya arayüz başarıyla çalışıyor)."
        except Exception as e: return f"Çalıştırma hatası: {e}"

    def _try_auto_install(self, error_out: str, proj_dir: Path) -> bool:
        match = re.search(r"No module named ['\"]([a-zA-Z0-9_\-\.]+)['\"]", error_out, re.IGNORECASE)
        if not match: return False
        pkg = match.group(1).replace("_", "-").split(".")[0]
        print(f"[DevAgent] 🔧 Eksik kütüphane otomatik kuruluyor: {pkg}")
        return subprocess.run([sys.executable, "-m", "pip", "install", pkg], capture_output=True, cwd=str(proj_dir)).returncode == 0

    def _fix_files(self, err_out: str, desc: str, all_files: list[dict], file_codes: dict[str, str], lang: str, proj_dir: Path, entry: str) -> dict[str, str]:
        model = self._get_model()
        err_file, err_line = self._parse_traceback(err_out, list(file_codes.keys()))
        files_to_fix = [err_file] if err_file else [entry]
        updated = {}

        for fix_path in files_to_fix:
            curr_code = file_codes.get(fix_path, "")
            other_ctx = "".join(f"\n--- {fp} ---\n{code[:1000]}\n" for fp, code in file_codes.items() if fp != fix_path)
            prompt = f"""You are a {lang} debugger. Fix the broken file.
Error: {err_out[:2000]}
Current code ({fix_path}):\n{curr_code}
Context:\n{other_ctx}
Output ONLY complete fixed code (no markdown). Do not remove working features."""
            try:
                resp = model.generate_content(prompt)
                fixed = self._strip_fences(resp.text)
                (proj_dir / fix_path).write_text(fixed, encoding="utf-8")
                updated[fix_path] = fixed
                print(f"[DevAgent] 🔧 Düzeltildi: {fix_path}")
            except Exception as e:
                print(f"[DevAgent] ⚠️ Düzeltme başarısız ({fix_path}): {e}")
        return updated

    def execute(self, params: dict) -> str:
        desc = params.get("description", "").strip()
        lang = params.get("language", "python").strip()
        timeout = int(params.get("timeout", 30))

        if not desc: return "Lütfen geliştirmemi istediğiniz projeyi tarif edin."
        print(f"[DevAgent] Proje planlanıyor: {desc[:50]}...")

        try: plan = self._plan_project(desc, lang)
        except Exception as e: return f"Planlama başarısız: {e}"

        proj_name = re.sub(r"[^\w\-]", "_", plan.get("project_name", "sirius_proje"))
        proj_dir = self.projects_dir / proj_name
        proj_dir.mkdir(parents=True, exist_ok=True)
        
        files = plan.get("files", [])
        entry_point = plan.get("entry_point", "main.py")
        run_cmd = plan.get("run_command", f"python {entry_point}")
        
        file_codes = {}
        sorted_files = sorted(files, key=lambda f: len(f.get("imports", [])))

        for fi in sorted_files:
            try:
                file_codes[fi["path"]] = self._write_file(fi, desc, files, lang, proj_dir, file_codes)
                time.sleep(0.5)
            except Exception as e:
                print(f"Dosya yazılamadı: {e}")

        if not file_codes: return "Proje dosyaları oluşturulamadı."
        print(self._install_dependencies(plan.get("dependencies", []), proj_dir))
        self._open_vscode(proj_dir)

        last_out, auto_installs = "", 0
        for attempt in range(1, self.max_fix_attempts + 1):
            print(f"[DevAgent] Test ediliyor (Deneme {attempt}/{self.max_fix_attempts})...")
            last_out = self._run_project(run_cmd, proj_dir, timeout)
            
            if not self._has_error(last_out, run_cmd):
                return f"Proje '{proj_name}' başarıyla oluşturuldu ve test edildi patron! \nKonum: {proj_dir}"

            if attempt == self.max_fix_attempts: break

            err_type = self._classify_error(last_out)
            if err_type == "dependency_error" and auto_installs < 3:
                if self._try_auto_install(last_out, proj_dir):
                    auto_installs += 1
                    continue

            print(f"[DevAgent] Hata düzeltiliyor ({err_type})...")
            file_codes.update(self._fix_files(last_out, desc, files, file_codes, lang, proj_dir, entry_point))
            time.sleep(1)

        return f"Projeyi {self.max_fix_attempts} denemeye rağmen tamamen düzeltemedim patron. VS Code üzerinden manuel kontrol edebilirsiniz.\nSon Hata: {last_out[:300]}"