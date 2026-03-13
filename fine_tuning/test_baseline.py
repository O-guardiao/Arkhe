"""
Teste de Baseline — qwen3.5:4b via Ollama no motor RLM
======================================================
Avalia o modelo sem fine-tuning em 5 dimensões críticas:
  1. Geração de código Python correto
  2. Aderência ao formato REPL (```repl``` blocks + FINAL_VAR)
  3. Uso de sub_rlm para divisão de tarefas complexas
  4. Uso de sub_rlm_parallel para tarefas independentes
  5. Raciocínio recursivo multi-passo

Uso:
    cd rlm-main
    python fine_tuning/test_baseline.py

Resultados salvos em fine_tuning/baseline_results.json
"""

import json
import time
import traceback
from datetime import datetime
from pathlib import Path

from rlm import RLM
from rlm.logger import RLMLogger

# ─── Configuração ─────────────────────────────────────────────────────────────

MODEL = "qwen3.5:4b"
OLLAMA_BASE_URL = "http://localhost:11434/v1"
RESULTS_FILE = Path(__file__).parent / "baseline_results.json"

# ─── Casos de teste ───────────────────────────────────────────────────────────

TEST_CASES = [
    # ── Nível 1: Código básico ─────────────────────────────────────────────────
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
            "resultado_correto": "55",  # fib(10) = 55
        },
    },
    # ── Nível 2: Algoritmo com recursão ───────────────────────────────────────
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
    # ── Nível 3: Uso de sub_rlm ───────────────────────────────────────────────
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
    # ── Nível 4: sub_rlm_parallel ─────────────────────────────────────────────
    {
        "id": "T04_parallel",
        "nivel": 4,
        "categoria": "sub_rlm_parallel",
        "descricao": "Tarefas paralelas com sub_rlm_parallel",
        "prompt": (
            "Use sub_rlm_parallel para executar em paralelo estas 3 análises sobre listas Python:\n"
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
    # ── Nível 5: Raciocínio multi-passo complexo ──────────────────────────────
    {
        "id": "T05_multistep",
        "nivel": 5,
        "categoria": "raciocínio multi-passo",
        "descricao": "Pipeline de processamento com múltiplos REPL blocks",
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
            "multiplos_blocks": None,  # verificado manualmente
        },
    },
]


# ─── Motor de execução ────────────────────────────────────────────────────────

def criar_rlm() -> RLM:
    """Cria instância RLM apontando para Ollama local."""
    return RLM(
        backend="openai",
        backend_kwargs={
            "model_name": MODEL,
            "api_key": "ollama",  # Ollama não valida API key
            "base_url": OLLAMA_BASE_URL,
        },
        environment="local",
        environment_kwargs={},
        max_depth=1,
        max_iterations=15,
        verbose=True,
    )


def avaliar_criterios(resposta: str, criterios: dict) -> dict:
    """Verifica cada critério na resposta do modelo."""
    scores = {}
    for nome, valor in criterios.items():
        if valor is None:
            # Critério manual — conta blocos repl
            count = resposta.count("```repl")
            scores[nome] = {"ok": count >= 2, "detalhe": f"{count} blocos repl"}
        elif isinstance(valor, list):
            # Qualquer string da lista deve estar presente
            encontrado = any(v in resposta for v in valor)
            scores[nome] = {"ok": encontrado, "detalhe": f"buscando: {valor}"}
        else:
            scores[nome] = {"ok": valor in resposta, "detalhe": f"buscando: '{valor}'"}
    return scores


def executar_teste(tc: dict, rlm: RLM) -> dict:
    """Executa um caso de teste e retorna resultado completo."""
    print(f"\n{'='*60}")
    print(f"[{tc['id']}] {tc['descricao']} (Nível {tc['nivel']})")
    print(f"Categoria: {tc['categoria']}")
    print("─" * 60)

    inicio = time.perf_counter()
    erro = None
    resposta = ""
    resultado_final = None

    try:
        resultado = rlm.completion(tc["prompt"])
        resposta = resultado.answer if hasattr(resultado, "answer") else str(resultado)
        resultado_final = resposta
    except Exception as e:
        erro = traceback.format_exc()
        print(f"  ❌ ERRO: {e}")

    elapsed = time.perf_counter() - inicio

    # Avaliar critérios
    scores = avaliar_criterios(resposta, tc["criterios"]) if resposta else {}
    passou = all(s["ok"] for s in scores.values()) if scores else False

    # Imprimir resumo
    print(f"\n  Tempo: {elapsed:.1f}s")
    print(f"  Critérios:")
    for nome, s in scores.items():
        icon = "✅" if s["ok"] else "❌"
        print(f"    {icon} {nome}: {s['detalhe']}")

    status = "✅ PASSOU" if passou and not erro else "❌ FALHOU"
    print(f"\n  Status: {status}")

    # Prévia da resposta
    if resposta:
        preview = resposta[:300].replace("\n", " ")
        print(f"  Resposta (prévia): {preview}...")

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


# ─── Runner principal ─────────────────────────────────────────────────────────

def main():
    print("\n" + "█" * 60)
    print("  BASELINE TEST — qwen3.5:4b sem fine-tuning")
    print(f"  Modelo: {MODEL} @ {OLLAMA_BASE_URL}")
    print(f"  Data: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("█" * 60)

    resultados = []
    total = len(TEST_CASES)

    for tc in TEST_CASES:
        # Cada teste usa RLM fresco (sem estado acumulado)
        rlm = criar_rlm()
        resultado = executar_teste(tc, rlm)
        resultados.append(resultado)

    # ── Resumo final ───────────────────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("  RESUMO FINAL")
    print("─" * 60)

    passou_count = sum(1 for r in resultados if r["passou"])
    tempo_total = sum(r["tempo_s"] for r in resultados)

    for r in resultados:
        icon = "✅" if r["passou"] else "❌"
        print(f"  {icon} [{r['id']}] {r['descricao']} — {r['tempo_s']}s")

    print(f"\n  Score: {passou_count}/{total} ({100*passou_count//total}%)")
    print(f"  Tempo total: {tempo_total:.1f}s")

    # Avaliar por nivel
    print("\n  Por nível de dificuldade:")
    for nivel in range(1, 6):
        nivel_res = [r for r in resultados if r["nivel"] == nivel]
        if nivel_res:
            ok = sum(1 for r in nivel_res if r["passou"])
            print(f"    Nível {nivel}: {ok}/{len(nivel_res)}")

    # ── Salvar JSON ───────────────────────────────────────────────────────────
    output = {
        "meta": {
            "modelo": MODEL,
            "data": datetime.now().isoformat(),
            "score": f"{passou_count}/{total}",
            "tempo_total_s": round(tempo_total, 2),
            "tipo": "baseline_sem_finetune",
        },
        "resultados": resultados,
    }

    RESULTS_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Resultados salvos em: {RESULTS_FILE}")
    print("═" * 60 + "\n")

    return output


if __name__ == "__main__":
    main()
