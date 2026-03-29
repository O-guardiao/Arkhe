+++
name = "image_gen"
description = "Generate and edit images using OpenAI Images API (DALL-E 3, gpt-image-1). Returns URL or base64 of generated image. Use when: user asks to generate image, create illustration, design visual, draw, render picture, or edit existing image. NOT for: screenshots (use playwright skill), audio/video (use whisper/voice skill), text generation."
tags = ["imagem", "gerar imagem", "ilustração", "DALL-E", "gpt-image", "criar imagem", "desenhar", "render", "image generation", "arte", "visual"]
priority = "contextual"

[sif]
signature = "image_gen(prompt: str, size: str = '1024x1024', model: str = 'gpt-image-1', quality: str = 'auto') -> dict"
prompt_hint = "Use para gerar imagens a partir de descrição textual. Retorna URL da imagem. Forneça prompts detalhados em inglês para melhores resultados."
short_sig = "image_gen(prompt,size='1024x1024')→{}"
compose = ["browser", "filesystem", "notion", "email", "telegram_bot", "whatsapp"]
examples_min = ["gerar imagem a partir de prompt textual e salvar/enviar"]
impl = """
def image_gen(prompt, size='1024x1024', model='gpt-image-1', quality='auto'):
    import urllib.request, json, os
    api_key = os.environ.get('OPENAI_API_KEY', '')
    if not api_key:
        return {"error": "OPENAI_API_KEY não configurada"}
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": quality,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        data = json.loads(resp.read())
    except Exception as e:
        return {"error": str(e), "prompt": prompt}
    img = data.get("data", [{}])[0]
    return {
        "url": img.get("url", ""),
        "revised_prompt": img.get("revised_prompt", ""),
        "model": model,
        "size": size,
    }
"""

[requires]
bins = []

[runtime]
estimated_cost = 2.0
risk_level = "medium"
side_effects = ["http_request", "api_cost"]
postconditions = ["image_url_returned"]
fallback_policy = "describe_image_in_text_or_suggest_alternative"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "generate image picture illustration art visual DALL-E gpt-image render create draw"
example_queries = ["gere uma imagem de", "crie uma ilustração", "desenhe um logo"]
+++

# Image Generation Skill

Gera imagens via OpenAI Images API (DALL-E 3 / gpt-image-1).

## Quando usar

✅ **USE quando:**
- "Gere uma imagem de um gato pilotando um avião"
- "Crie uma ilustração para o artigo sobre IA"
- "Desenhe um diagrama conceitual de..."
- "Faça um logo minimalista para..."
- "Renderize uma cena futurista de..."

❌ **NÃO use quando:**
- Screenshot de site → use `playwright` skill
- Edição de PDF → use arquivo local + ferramentas
- Áudio/vídeo → use `whisper` / `voice` skills

## Função injetada no REPL

```python
# Gerar imagem simples
resultado = image_gen("A futuristic city at sunset, cyberpunk style, detailed")
print(resultado["url"])  # URL da imagem gerada

# Tamanhos disponíveis: 1024x1024, 1024x1792, 1792x1024
resultado = image_gen(
    "Minimalist logo for an AI company called Arkhe",
    size="1024x1024",
    quality="high",
)
print(resultado["revised_prompt"])
```

## Download da imagem gerada

```python
import urllib.request

resultado = image_gen("A serene mountain lake at dawn")
if "url" in resultado and resultado["url"]:
    urllib.request.urlretrieve(resultado["url"], "/tmp/generated_image.png")
    print("Salvo em /tmp/generated_image.png")
```

## Enviar imagem gerada via Telegram

```python
resultado = image_gen("Cute robot reading a book")
if resultado.get("url"):
    # Primeiro baixa
    urllib.request.urlretrieve(resultado["url"], "/tmp/robo.png")
    # Depois envia via telegram
    send_photo(chat_id="USER_ID", file_path="/tmp/robo.png", caption="Robô lendo!")
```

## Modelos disponíveis

| Modelo | Qualidade | Custo | Notas |
|--------|-----------|-------|-------|
| `gpt-image-1` | Excelente | ~$0.04/img | Mais recente, melhor para tudo |
| `dall-e-3` | Ótima | ~$0.04/img | Bom para arte conceitual |
| `dall-e-2` | Boa | ~$0.02/img | Mais rápido, menor qualidade |

## Dicas para prompts eficazes

- Escreva prompts em **inglês** para melhores resultados
- Seja específico: "A photorealistic red fox in a snow-covered forest" > "raposa"
- Especifique estilo: "watercolor", "3D render", "pixel art", "photorealistic"
- Inclua detalhes de composição: "close-up", "aerial view", "golden hour lighting"
