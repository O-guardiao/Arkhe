+++
name = "whisper"
description = "Transcribe audio files to text using OpenAI Whisper API or local whisper CLI. Supports mp3, mp4, wav, m4a, webm, ogg. Use when: user asks to transcrever áudio, converter fala em texto, extrair texto de gravação, legendar vídeo, ou speech-to-text. NOT for: text-to-speech (use voice skill), music generation, audio editing."
tags = ["transcrever", "transcrição", "áudio", "fala", "speech-to-text", "whisper", "legendar", "gravação", "podcast", "stt", "voz para texto"]
priority = "contextual"

[sif]
signature = "whisper_transcribe(file_path: str, language: str = 'pt', model: str = 'whisper-1') -> dict"
prompt_hint = "Use para transcrever áudio em texto. Suporta mp3/wav/m4a/ogg. Retorna texto + idioma detectado."
short_sig = "whisper_transcribe(path,lang='pt')→{}"
compose = ["summarize", "filesystem", "notion", "email"]
examples_min = ["transcrever arquivo de áudio para texto"]
impl = """
def whisper_transcribe(file_path, language='pt', model='whisper-1'):
    import urllib.request, json, os, mimetypes
    api_key = os.environ.get('OPENAI_API_KEY', '')
    if not api_key:
        return {"error": "OPENAI_API_KEY não configurada"}
    if not os.path.isfile(file_path):
        return {"error": f"Arquivo não encontrado: {file_path}"}
    mime = mimetypes.guess_type(file_path)[0] or 'audio/mpeg'
    fname = os.path.basename(file_path)
    boundary = '----RLMBoundary9876543210'
    parts = []
    parts.append(f'--{boundary}\\r\\nContent-Disposition: form-data; name="model"\\r\\n\\r\\n{model}'.encode())
    parts.append(f'--{boundary}\\r\\nContent-Disposition: form-data; name="language"\\r\\n\\r\\n{language}'.encode())
    parts.append(f'--{boundary}\\r\\nContent-Disposition: form-data; name="response_format"\\r\\n\\r\\nverbose_json'.encode())
    with open(file_path, 'rb') as f:
        file_data = f.read()
    file_header = f'--{boundary}\\r\\nContent-Disposition: form-data; name="file"; filename="{fname}"\\r\\nContent-Type: {mime}\\r\\n\\r\\n'.encode()
    body = b'\\r\\n'.join(parts) + b'\\r\\n' + file_header + file_data + f'\\r\\n--{boundary}--\\r\\n'.encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        data = json.loads(resp.read())
    except Exception as e:
        return {"error": str(e), "file": file_path}
    return {
        "text": data.get("text", ""),
        "language": data.get("language", language),
        "duration_seconds": data.get("duration", 0),
        "segments": len(data.get("segments", [])),
        "file": file_path,
    }
"""

[requires]
bins = []

[runtime]
estimated_cost = 1.0
risk_level = "low"
side_effects = ["http_request", "api_cost", "file_read"]
postconditions = ["transcription_text_returned"]
fallback_policy = "try_local_whisper_or_explain_manual_steps"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "transcribe audio speech text whisper podcast recording voice recognition stt"
example_queries = ["transcreva este áudio", "converta fala em texto", "extraia texto desta gravação"]
+++

# Whisper Skill

Transcrição de áudio → texto via OpenAI Whisper API ou CLI local.

## Quando usar

✅ **USE quando:**
- "Transcreva este áudio"
- "Converta a gravação em texto"
- "Extraia o que foi dito neste podcast"
- "Legende este vídeo"
- "Qual o conteúdo deste arquivo de áudio?"

❌ **NÃO use quando:**
- Texto → voz (TTS) → use `voice` skill
- Resumir URL/YouTube → use `summarize` skill
- Editar áudio → use ferramentas especializadas

## Função injetada no REPL

```python
# Transcrever arquivo de áudio
resultado = whisper_transcribe("/tmp/reuniao.mp3")
print(resultado["text"])
print(f"Duração: {resultado['duration_seconds']}s, Segmentos: {resultado['segments']}")

# Especificar idioma
resultado = whisper_transcribe("/tmp/interview.wav", language="en")
print(resultado["text"])
```

## Pipeline: Transcrever + Resumir

```python
# 1. Transcreve
trans = whisper_transcribe("/tmp/palestra.mp3", language="pt")
texto_completo = trans["text"]

# 2. Passa para o LLM resumir (direto no REPL)
FINAL_VAR("texto_completo")
# Depois peça: "Resuma os pontos principais deste texto"
```

## Whisper Local (sem API — requer instalação)

```python
import subprocess, json

def whisper_local(file_path: str, model: str = "base", language: str = "pt") -> dict:
    """Transcrição local via whisper CLI (pip install openai-whisper)."""
    cmd = [
        "whisper", file_path,
        "--model", model,
        "--language", language,
        "--output_format", "json",
        "--output_dir", "/tmp/whisper_out",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        return {"error": result.stderr}
    
    import os, glob
    base = os.path.splitext(os.path.basename(file_path))[0]
    json_file = f"/tmp/whisper_out/{base}.json"
    if os.path.exists(json_file):
        with open(json_file) as f:
            data = json.load(f)
        return {"text": data.get("text", ""), "segments": len(data.get("segments", []))}
    return {"error": "Arquivo de saída não encontrado"}
```

## Formatos suportados

| Formato | Extensão | Suporte |
|---------|----------|---------|
| MP3 | `.mp3` | ✅ API + Local |
| WAV | `.wav` | ✅ API + Local |
| M4A | `.m4a` | ✅ API + Local |
| OGG | `.ogg` | ✅ API + Local |
| WebM | `.webm` | ✅ API + Local |
| MP4 | `.mp4` | ✅ API + Local |
| FLAC | `.flac` | ✅ Local only |

## Limites

- API: máximo **25 MB** por arquivo
- Arquivos maiores: divida com `ffmpeg -i big.mp3 -f segment -segment_time 600 part_%03d.mp3`
- Timeout: 120s padrão na API, 300s local
