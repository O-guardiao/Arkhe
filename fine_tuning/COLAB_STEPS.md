# Fine-tuning Qwen3.5-4B para RLM — Guia Colab T4

## Hardware alvo
- Google Colab Free Tier: **NVIDIA T4 (15 GB VRAM)**
- Modelo: `Qwen/Qwen3.5-4B`
- Método: **QLoRA 4-bit** (~5-6 GB VRAM durante treino)

---

## Passo 1 — Abrir Colab e configurar GPU

1. Acesse [colab.research.google.com](https://colab.research.google.com)
2. **Runtime → Change runtime type → T4 GPU**
3. Verifique que tem GPU:
```python
!nvidia-smi
```

---

## Passo 2 — Instalar LLaMA Factory

```python
!git clone https://github.com/hiyouga/LLaMA-Factory.git
%cd LLaMA-Factory
!pip install -e ".[torch,bitsandbytes]" -q
```

---

## Passo 3 — Montar Google Drive

```python
from google.colab import drive
drive.mount('/content/drive')

import os
os.makedirs('/content/drive/MyDrive/rlm_finetune/data', exist_ok=True)
os.makedirs('/content/drive/MyDrive/rlm_finetune/output', exist_ok=True)
```

---

## Passo 4 — Upload dos arquivos de treino

Faça upload dos dois arquivos (ou copie do Drive):

```python
# Opção A: upload direto
from google.colab import files
uploaded = files.upload()
# Selecione: dataset_rlm_code.json e qwen3_5_rlm_qlora.yaml

# Opção B: se já estão no Drive
!cp /content/drive/MyDrive/rlm_finetune/dataset_rlm_code.json \
    /content/drive/MyDrive/rlm_finetune/data/dataset_rlm_code.json
!cp /content/drive/MyDrive/rlm_finetune/qwen3_5_rlm_qlora.yaml \
    /content/LLaMA-Factory/qwen3_5_rlm_qlora.yaml
```

---

## Passo 5 — Registrar dataset no LLaMA Factory

O LLaMA Factory precisa conhecer seu dataset via `dataset_info.json`:

```python
import json

dataset_info_path = "/content/LLaMA-Factory/data/dataset_info.json"

with open(dataset_info_path) as f:
    dataset_info = json.load(f)

# Adiciona entrada para nosso dataset
dataset_info["rlm_code"] = {
    "file_name": "/content/drive/MyDrive/rlm_finetune/data/dataset_rlm_code.json",
    "formatting": "sharegpt",
    "columns": {
        "messages": "messages"
    }
}

with open(dataset_info_path, "w") as f:
    json.dump(dataset_info, f, indent=2)

print("Dataset registrado com sucesso!")
```

---

## Passo 6 — Iniciar treinamento

```python
%cd /content/LLaMA-Factory

!llamafactory-cli train qwen3_5_rlm_qlora.yaml
```

Tempo estimado no T4 gratuito:
- ~15 exemplos × 3 épocas = ~45 passos
- **~10-20 minutos** para dataset pequeno (15 exemplos)
- Para dataset de 200+ exemplos: ~2-4 horas

---

## Passo 7 — Monitorar treinamento

Durante o treino, você verá logs assim:
```
{'loss': 1.234, 'learning_rate': 0.0002, 'epoch': 0.5}
{'loss': 0.987, 'learning_rate': 0.00015, 'epoch': 1.0}
```

**Loss-alvo**: abaixo de 0.5 é ótimo para dataset pequeno.

---

## Passo 8 — Exportar LoRA e converter para GGUF

### 8a — Mesclar LoRA com modelo base

Crie o arquivo `merge_config.yaml`:

```python
merge_config = """
model_name_or_path: Qwen/Qwen3.5-4B
adapter_name_or_path: /content/drive/MyDrive/rlm_finetune/output
template: qwen3_5
finetuning_type: lora
export_dir: /content/drive/MyDrive/rlm_finetune/merged_model
export_size: 4
export_device: cuda
export_legacy_format: false
"""

with open("/content/LLaMA-Factory/merge_config.yaml", "w") as f:
    f.write(merge_config)

!llamafactory-cli export merge_config.yaml
```

### 8b — Converter para GGUF (para usar no Ollama local)

```python
# Instalar llama.cpp para conversão
!git clone https://github.com/ggerganov/llama.cpp.git /content/llama.cpp
!pip install -r /content/llama.cpp/requirements.txt -q

# Converter para GGUF Q4_K_M (melhor balanço qualidade/tamanho)
!python /content/llama.cpp/convert_hf_to_gguf.py \
    /content/drive/MyDrive/rlm_finetune/merged_model \
    --outfile /content/drive/MyDrive/rlm_finetune/qwen3.5-4b-rlm.gguf \
    --outtype q4_k_m

print("GGUF gerado!")
```

---

## Passo 9 — Usar o modelo fine-tuned no Ollama (local)

Baixe o arquivo `.gguf` do Google Drive para sua máquina, depois:

### Criar Modelfile

```bash
# Crie um arquivo chamado Modelfile na mesma pasta do .gguf
FROM ./qwen3.5-4b-rlm.gguf

PARAMETER temperature 0.6
PARAMETER top_p 0.95
PARAMETER num_ctx 4096

SYSTEM """Você é um agente RLM especializado em geração de código Python recursivo.
Use blocos repl, sub_rlm(), sub_rlm_parallel() e FINAL_VAR() conforme necessário."""
```

### Registrar no Ollama

```powershell
ollama create qwen3.5-4b-rlm -f Modelfile
ollama run qwen3.5-4b-rlm
```

---

## Aumentar o dataset (recomendado)

O dataset atual tem **15 exemplos** — suficiente para provar o conceito.
Para resultado real, adicione mais exemplos ao `dataset_rlm_code.json`.

**Padrões prioritários para adicionar:**
1. `sub_rlm_parallel` com 4+ tarefas independentes
2. Loops de refinamento (iterar com `llm_query` até critério de parada)
3. Recursão profunda: `sub_rlm` dentro de função que também usa `sub_rlm`
4. Tratamento de erros no REPL com try/except
5. Contexto dinâmico com `context.get()`

---

## Checklist rápido

- [ ] Colab aberto com T4 GPU ativo
- [ ] LLaMA Factory instalado
- [ ] Drive montado
- [ ] `dataset_rlm_code.json` na pasta de dados
- [ ] `qwen3_5_rlm_qlora.yaml` no diretório LLaMA Factory
- [ ] Dataset registrado em `dataset_info.json`
- [ ] Treino iniciado: `llamafactory-cli train ...`
- [ ] LoRA salvo no Drive
- [ ] Modelo mesclado e exportado como GGUF
- [ ] Modelfile criado e modelo registrado no Ollama local
