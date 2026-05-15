# ai_core/llm_router.py

from litellm import completion

class LLMRouter:
    def __init__(self, default_model="ollama/llama3"):
        # Varsayılan model olarak yerel cihazındaki Ollama Llama3'ü seçtik
        self.default_model = default_model
        
        # Ollama'nın varsayılan API adresi
        self.api_base = "http://localhost:11434" 

    def ask(self, prompt, system_prompt="Sen Sirius'sun, yetenekli bir masaüstü asistanısın."):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        try:
            response = completion(
                model=self.default_model,
                messages=messages,
                api_base=self.api_base
            )
            return response.choices[0].message.content
            
        except Exception as e:
            return f"Model ile iletişim kurulurken hata oluştu: {str(e)}"