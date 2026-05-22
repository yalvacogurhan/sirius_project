# plugins/web_search.py

class WebSearchManager:
    def __init__(self):
        print("🌐 Sirius Akıllı Web Arama Motoru Aktif! (Gemini & DuckDuckGo)")
        self.gemini_key = "" # main.py'den gelecek

    def _gemini_search(self, query: str) -> str:
        if not self.gemini_key:
            raise ValueError("Gemini API anahtarı eksik.")
        from google import genai
        
        client = genai.Client(api_key=self.gemini_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=query,
            config={"tools": [{"google_search": {}}]},
        )

        text = ""
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text

        text = text.strip()
        if not text: raise ValueError("Gemini boş bir yanıt döndürdü.")
        return text

    def _ddg_search(self, query: str, max_results: int = 6) -> list[dict]:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            raise RuntimeError("duckduckgo-search kütüphanesi eksik.")

        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title":   r.get("title",  ""),
                    "snippet": r.get("body",   ""),
                    "url":     r.get("href",   ""),
                })
        return results

    def _format_ddg(self, query: str, results: list[dict]) -> str:
        if not results: return f"'{query}' için sonuç bulunamadı patron."

        lines = [f"'{query}' için Arama Sonuçları:\n"]
        for i, r in enumerate(results, 1):
            if r.get("title"):   lines.append(f"{i}. {r['title']}")
            if r.get("snippet"): lines.append(f"   {r['snippet']}")
            if r.get("url"):     lines.append(f"   {r['url']}\n")
        return "\n".join(lines).strip()

    def _compare(self, items: list[str], aspect: str) -> str:
        query = f"{', '.join(items)} seçeneklerini '{aspect}' açısından karşılaştır. Lütfen spesifik veriler ve gerçekler sun."
        try:
            return self._gemini_search(query)
        except Exception as e:
            print(f"[WebArama] ⚠️ Gemini karşılaştırması başarısız ({e}) — DuckDuckGo deneniyor.")
            all_results = {}
            for item in items:
                try: all_results[item] = self._ddg_search(f"{item} {aspect}", max_results=3)
                except: all_results[item] = []

            lines = [f"Karşılaştırma Sonucu — {aspect.upper()}", "─" * 40]
            for item in items:
                lines.append(f"\n▶ {item}")
                for r in all_results.get(item, [])[:2]:
                    if r.get("snippet"): lines.append(f"  • {r['snippet']}")
            return "\n".join(lines)

    def execute(self, params: dict) -> str:
        query  = params.get("query", "").strip()
        mode   = params.get("mode",  "search").lower().strip()
        items  = params.get("items", [])
        aspect = params.get("aspect", "genel").strip() or "genel"

        if not query and not items:
            return "Lütfen aramam için bir konu belirtin patron."

        if items and mode != "compare": mode = "compare"

        print(f"[WebArama] 🔍 Sorgu: '{query}' | Mod: {mode}")

        try:
            if mode == "compare" and items:
                print(f"[WebArama] 📊 Karşılaştırılıyor: {items}")
                return self._compare(items, aspect)

            print("[WebArama] 🌐 Gemini Arama Motoru deneniyor...")
            try:
                result = self._gemini_search(query)
                print("[WebArama] ✅ Gemini başarılı.")
                return result
            except Exception as e:
                print(f"[WebArama] ⚠️ Gemini başarısız oldu ({e}) — DuckDuckGo'ya geçiliyor...")
                results = self._ddg_search(query)
                result  = self._format_ddg(query, results)
                print(f"[WebArama] ✅ DDG: {len(results)} sonuç bulundu.")
                return result

        except Exception as e:
            print(f"[WebArama] ❌ Tüm arama motorları çöktü: {e}")
            return f"İnternet araması başarısız oldu patron: {e}"