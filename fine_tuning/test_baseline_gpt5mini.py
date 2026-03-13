"""
Teste de Baseline — gpt-5-mini via OpenAI API no motor RLM (fork)
==================================================================
Mesmo protocolo do teste qwen3.5:4b para comparação direta.
  1. Geração de código Python correto
  2. Aderência ao formato REPL (```repl``` blocks + FINAL_VAR)
  3. Uso de sub_rlm para divisão de tarefas complexas
  4. Uso de sub_rlm_parallel para tarefas independentes
  5. Raciocínio multi-passo

Uso:
    cd RLM_OpenClaw_Engine/rlm-main
    py -3.13 fine_tuning/test_baseline_gpt5mini.py

Resultados salvos em fine_tuning/baseline_results_gpt5mini.json
"""

import json
import os
import sys
import time
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from rlm import RLM
from rlm.logger import RLMLogger

# Carrega .env do diretório rlm-main (pai da pasta fine_tuning)
_ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(_ENV_FILE)

# ─── Configuração ─────────────────────────────────────────────────────────────

MODEL = "gpt-5-mini"
RESULTS_FILE = Path(__file__).parent / "baseline_results_gpt5mini.json"

# ─── Casos de teste (idênticos ao qwen baseline) ─────────────────────────────

TEST_CASES = [
    {
        "id": "T01_codigo_basico",
        "nivel": 1,
        "categoria": "geração de código",
        "descricao": "Fibonacci iterativo simples",
        "prompt": "Escreva uma função Python fib(n) que retorna o n-ésimo número de Fibonacci de forma iterativa (não recursiva). Teste com n=10.",
        "criterios": {
            "usa_repl_block": "```repl",
            "usa_final": "FINAL",
            "contem_def_fib": "def fib",
            "resultado_correto": "55",
        },
    },
    {
        "id": "T02_recursao_direta",
        "nivel": 2,
        "categoria": "recursão",
        "descricao": "Quicksort recursivo",
        "prompt": (
            "Implemente quicksort recursivo em Python. "
            "Teste com a lista [3, 6, 8, 10, 1, 2, 1] e mostre a lista ordenada."
        ),
        "criterios": {
            "usa_repl_block": "```repl",
            "usa_final": "FINAL",
            "contem_quicksort": "quicksort",
            "resultado_correto": "[1, 1, 2, 3, 6, 8, 10]",
        },
    },
    {
        "id": "T03_sub_rlm",
        "nivel": 3,
        "categoria": "sub_rlm",
        "descricao": "Delegação de subtarefa com sub_rlm",
        "prompt": (
            "Use sub_rlm para implementar um sistema de validação de CPF brasileiro. "
            "Primeiro use sub_rlm para obter o algoritmo dos dígitos verificadores, "
            "depois implemente a função valida_cpf(cpf: str) -> bool completa."
        ),
        "criterios": {
            "usa_repl_block": "```repl",
            "usa_sub_rlm": "sub_rlm(",
            "usa_final": "FINAL",
            "contem_valida_cpf": "valida_cpf",
        },
    },
    {
        "id": "T04_parallel",
        "nivel": 4,
        "categoria": "sub_rlm_parallel",
        "descricao": "Tarefas paralelas com sub_rlm_parallel",
        "prompt": (
            "Use sub_rlm_parallel para executar em paralelo estas 3 análises:\n"
            "1. Implementar bubble sort\n"
            "2. Implementar selection sort\n"
            "3. Implementar insertion sort\n"
            "Depois compare a complexidade de tempo dos 3 algoritmos."
        ),
        "criterios": {
            "usa_repl_block": "```repl",
            "usa_parallel": "sub_rlm_parallel",
            "usa_final": "FINAL",
            "menciona_complexidade": ["O(n²)", "O(n^2)", "O(n2)"],
        },
    },
    {
        "id": "T05_multistep",
        "nivel": 5,
        "categoria": "raciocínio multi-passo",
        "descricao": "Pipeline com múltiplos blocos REPL",
        "prompt": (
            "Crie um pipeline de análise de texto em etapas:\n"
            "1. Defina uma lista de 5 frases sobre tecnologia\n"
            "2. Para cada frase, use llm_query para classificar o sentimento (positivo/negativo/neutro)\n"
            "3. Use sub_rlm_parallel para processar as frases em paralelo\n"
            "4. Consolide os resultados e mostre um resumo com contagem por sentimento\n"
            "Retorne o resumo consolidado com FINAL_VAR."
        ),
        "criterios": {
            "usa_repl_block": "```repl",
            "usa_llm_query": "llm_query",
            "usa_parallel": "sub_rlm_parallel",
            "usa_final_var": "FINAL_VAR",
            "multiplos_blocks": None,
        },
    },
]

# ─── Avaliador ────────────────────────────────────────────────────────────────

def extrair_texto_do_log(log_dir: str) -> str:
    """Lê o arquivo .jsonl gravado pelo RLMLogger e reconstrói texto completo.
    Captura: resposta LLM + código REPL + stdout de execução + final_answer.
    """
    partes = []
    jsonl_files = list(Path(log_dir).glob("*.jsonl"))
    if not jsonl_files:
        return ""
    jsonl_file = max(jsonl_files, key=lambda p: p.stat().st_mtime)
    with open(jsonl_file, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha:
                continue
            try:
                entry = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "iteration":
                continue
            resp = entry.get("response", "")
            if resp:
                partes.append(resp)
            for blk in entry.get("code_blocks", []):
                code = blk.get("code", "")
                if code:
                    partes.append(f"```repl\n{code}\n```")
                # captura stdout da execução (onde o resultado computado aparece)
                stdout = blk.get("result", {}).get("stdout", "")
                if stdout:
                    partes.append(stdout)
            # captura final_answer se presente na iteração
            final = entry.get("final_answer", "")
            if final:
                partes.append(final)
    return "\n".join(partes)


def avaliar_criterios(resposta: str, criterios: dict) -> dict:
    scores = {}
    for nome, valor in criterios.items():
        if valor is None:
            count = resposta.count("```repl")
            scores[nome] = {"ok": count >= 2, "detalhe": f"{count} blocos repl"}
        elif isinstance(valor, list):
            encontrado = any(v in resposta for v in valor)
            scores[nome] = {"ok": encontrado, "detalhe": f"buscando: {valor}"}
        else:
            scores[nome] = {"ok": valor in resposta, "detalhe": f"buscando: '{valor}'"}
    return scores


# ─── Runner ───────────────────────────────────────────────────────────────────

def criar_rlm(logger: RLMLogger) -> RLM:
    return RLM(
        backend="openai",
        backend_kwargs={"model_name": MODEL},
        max_depth=3,        # suporta sub_rlm (T03/T04)
        max_iterations=15,
        logger=logger,
        verbose=True,
    )


def executar_teste(tc: dict) -> dict:
    print(f"\n{'='*60}")
    print(f"[{tc['id']}] {tc['descricao']} (Nível {tc['nivel']})")
    print(f"Categoria: {tc['categoria']}")
    print("─" * 60)

    log_dir = tempfile.mkdtemp(prefix="rlm_test_")
    logger = RLMLogger(log_dir=log_dir)
    rlm = criar_rlm(logger)
    inicio = time.perf_counter()
    erro = None
    resposta = ""

    try:
        resultado = rlm.completion(tc["prompt"])
        resposta = extrair_texto_do_log(log_dir)
        # final answer do RLM (é o FINAL_VAR resolvido — contém o resultado real)
        if resultado and getattr(resultado, "response", ""):
            resposta = resultado.response + "\n" + resposta
    except Exception as e:
        erro = traceback.format_exc()
        try:
            resposta = extrair_texto_do_log(log_dir)
        except Exception:
            pass
        print(f"  ❌ ERRO: {e}")

    elapsed = time.perf_counter() - inicio
    scores = avaliar_criterios(resposta, tc["criterios"]) if resposta else {}
    passou = all(s["ok"] for s in scores.values()) if scores else False

    print(f"\n  Tempo: {elapsed:.1f}s")
    print(f"  Critérios:")
    for nome, s in scores.items():
        icon = "✅" if s["ok"] else "❌"
        print(f"    {icon} {nome}: {s['detalhe']}")
    print(f"\n  Status: {'✅ PASSOU' if passou and not erro else '❌ FALHOU'}")

    if resposta:
        print(f"  Resposta (prévia): {resposta[:250].replace(chr(10), ' ')}...")

    return {
        "id": tc["id"],
        "nivel": tc["nivel"],
        "categoria": tc["categoria"],
        "descricao": tc["descricao"],
        "passou": passou and not erro,
        "tempo_s": round(elapsed, 2),
        "scores": scores,
        "erro": erro,
        "resposta_preview": resposta[:500] if resposta else "",
        "resposta_completa": resposta,
    }


def main():
    if not os.getenv("OPENAI_API_KEY"):
        print(f"❌ OPENAI_API_KEY não encontrada em {_ENV_FILE}")
        return

    print("\n" + "█" * 60)
    print(f"  BASELINE TEST — {MODEL} via OpenAI API")
    print(f"  Motor: RLM fork (RLM_OpenClaw_Engine)")
    print(f"  Data: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("█" * 60)

    resultados = []
    for tc in TEST_CASES:
        resultado = executar_teste(tc)
        resultados.append(resultado)
        # Salva após cada teste
        output = {
            "meta": {
                "versao_rlm": "fork_v0.1.0",
                "modelo": MODEL,
                "data": datetime.now().isoformat(),
                "score": f"{sum(1 for r in resultados if r['passou'])}/{len(resultados)}",
                "tempo_total_s": round(sum(r["tempo_s"] for r in resultados), 2),
                "tipo": "baseline_sem_finetune",
            },
            "resultados": resultados,
        }
        RESULTS_FILE.write_text(
            json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    passou_count = sum(1 for r in resultados if r["passou"])
    tempo_total = sum(r["tempo_s"] for r in resultados)
    total = len(resultados)

    print("\n" + "─" * 60)
    print(f"  RESUMO FINAL — {MODEL}")
    print("─" * 60)
    for r in resultados:
        icon = "✅" if r["passou"] else "❌"
        print(f"  {icon} [{r['id']}] {r['descricao']} — {r['tempo_s']}s")

    print(f"\n  Score: {passou_count}/{total} ({100*passou_count//total if total else 0}%)")
    print(f"  Tempo total: {tempo_total:.1f}s")

    # Comparação com qwen (se resultado existir)
    qwen_file = RESULTS_FILE.parent / "baseline_results.json"
    if qwen_file.exists():
        qwen = json.loads(qwen_file.read_text(encoding="utf-8"))
        qwen_score = qwen.get("meta", {}).get("score", "?")
        qwen_tempo = qwen.get("meta", {}).get("tempo_total_s", "?")
        print(f"\n  Comparação:")
        print(f"    {MODEL}:      {passou_count}/{total} em {tempo_total:.0f}s")
        print(f"    qwen3.5:4b:  {qwen_score} em {qwen_tempo}s")

    print(f"\n  Resultados salvos em: {RESULTS_FILE}")
    print("═" * 60 + "\n")
    # Força saída: threads do ThreadPoolExecutor (não-daemon) bloqueiam o exit normal
    os._exit(0)


if __name__ == "__main__":
    main()
