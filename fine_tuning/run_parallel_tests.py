"""
Orquestrador paralelo — cada teste roda em seu próprio processo Python.

Problema resolvido:
  RLM cria threads (ThreadPoolExecutor) não-daemon que ficam vivas após o teste.
  Isso bloqueia o processo pai e impede o próximo teste de começar.

Solução:
  Cada teste = processo filho independente (subprocess.Popen).
  O filho termina com os._exit(0) → morre limpo, sem esperar threads.
  O pai pode iniciar o próximo teste imediatamente.

Uso:
    cd RLM_OpenClaw_Engine/rlm-main
    py -3.13 fine_tuning/run_parallel_tests.py              # todos em paralelo
    py -3.13 fine_tuning/run_parallel_tests.py --seq        # sequencial (mais seguro p/ API rate limit)
    py -3.13 fine_tuning/run_parallel_tests.py T01 T03 T05  # só esses testes
    py -3.13 fine_tuning/run_parallel_tests.py --workers 2  # máx 2 simultâneos
"""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# Força UTF-8 no stdout/stderr do processo pai (Windows cp1252 não suporta █ ─ ═)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─── Configuração ─────────────────────────────────────────────────────────────
_ROOT      = Path(__file__).parent.parent
_SCRIPT    = Path(__file__).parent / "run_single_test.py"
_OUT_FILE  = Path(__file__).parent / "baseline_results_gpt5mini.json"
_PYTHON    = sys.executable          # mesmo interpretador que está rodando agora
_TIMEOUT   = 700                     # segundos máximos por teste

# IDs na ordem desejada de execução
ALL_TEST_IDS = [
    "T01_codigo_basico",
    "T02_recursao_direta",
    "T03_sub_rlm",
    "T04_parallel",
    "T05_multistep",
]

# ─── Runner de um único teste via subprocess ──────────────────────────────────

def run_test_subprocess(test_id: str) -> dict:
    """
    Lança py -3.13 run_single_test.py <test_id> como processo filho.
    Retorna o dict de resultado (ou erro se timeout/crash).
    """
    t0 = time.perf_counter()
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [_PYTHON, "-X", "utf8", str(_SCRIPT), test_id],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_TIMEOUT,
            cwd=str(_ROOT),
            env=env,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # Acha a linha marcada com __RESULT__:
        result_line = None
        for line in reversed(stdout.splitlines()):
            if line.startswith("__RESULT__:"):
                result_line = line[len("__RESULT__:"):]
                break

        if result_line:
            return json.loads(result_line)

        # Não encontrou JSON — processo pode ter falhado
        elapsed = round(time.perf_counter() - t0, 2)
        print(f"\n  ⚠  [{test_id}] stdout sem __RESULT__. Últimas linhas:")
        for ln in stdout.splitlines()[-10:]:
            print(f"     {ln}")
        if stderr:
            print(f"  stderr: {stderr[-500:]}")
        return {
            "id": test_id, "passou": False,
            "tempo_s": elapsed,
            "scores": {}, "erro": f"sem __RESULT__ no stdout. rc={proc.returncode}",
            "resposta_preview": stdout[-200:],
            "resposta_completa": stdout,
        }

    except subprocess.TimeoutExpired as e:
        elapsed = round(time.perf_counter() - t0, 2)
        # Mata o processo filho que ficou preso
        try:
            e.process.kill()
        except Exception:
            pass
        print(f"\n  ⏱  [{test_id}] TIMEOUT após {elapsed:.0f}s — processo filho morto.")
        return {
            "id": test_id, "passou": False,
            "tempo_s": elapsed,
            "scores": {}, "erro": f"timeout ({_TIMEOUT}s)",
            "resposta_preview": "",
            "resposta_completa": "",
        }

    except Exception as exc:
        elapsed = round(time.perf_counter() - t0, 2)
        return {
            "id": test_id, "passou": False,
            "tempo_s": elapsed,
            "scores": {}, "erro": str(exc),
            "resposta_preview": "",
            "resposta_completa": "",
        }

# ─── Salva JSON incremental ───────────────────────────────────────────────────

def salvar_resultados(resultados: list[dict]):
    passou_count = sum(1 for r in resultados if r.get("passou"))
    total = len(resultados)
    output = {
        "meta": {
            "versao_rlm": "fork_v0.1.0",
            "modelo": "gpt-5-mini",
            "data": datetime.now().isoformat(),
            "score": f"{passou_count}/{total}",
            "tempo_total_s": round(sum(r.get("tempo_s", 0) for r in resultados), 2),
            "tipo": "baseline_sem_finetune",
            "modo_execucao": "subprocess_paralelo",
        },
        "resultados": resultados,
    }
    _OUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    # Parse argumentos
    sequential = "--seq" in args
    workers = 5
    for i, a in enumerate(args):
        if a == "--workers" and i + 1 < len(args):
            workers = int(args[i + 1])
            break

    # Quais testes rodar (filtra por T01, T02, etc. ou usa todos)
    ids_solicitados = [a for a in args if a.startswith("T") and not a.startswith("--")]
    if ids_solicitados:
        # Aceita "T01" ou "T01_codigo_basico"
        test_ids = [
            full for full in ALL_TEST_IDS
            if any(full.startswith(req) for req in ids_solicitados)
        ]
        if not test_ids:
            print(f"[ERRO] Nenhum teste encontrado para: {ids_solicitados}")
            sys.exit(1)
    else:
        test_ids = ALL_TEST_IDS

    modo = "SEQUENCIAL" if sequential else f"PARALELO (max {workers} simultaneos)"
    print(f"\n{'#'*64}")
    print(f"  BASELINE gpt-5-mini -- modo {modo}")
    print(f"  Testes: {test_ids}")
    print(f"  Cada teste = processo Python isolado (sem bloqueio cruzado)")
    print(f"{'#'*64}\n")

    resultados: list[dict] = []
    t_inicio = time.perf_counter()

    if sequential:
        # ── Modo sequencial: um de cada vez, mas cada um em subproceso isolado ──
        for tid in test_ids:
            print(f">> Iniciando [{tid}]...")
            result = run_test_subprocess(tid)
            resultados.append(result)
            icon = "[OK]" if result.get("passou") else "[FALHOU]"
            print(f"  {icon} [{tid}] {result.get('tempo_s', '?')}s")
            salvar_resultados(resultados)
    else:
        # ── Modo paralelo: ate `workers` testes simultaneos ──
        completados = {}
        print(f">> Lancando {len(test_ids)} processos filhos...\n")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run_test_subprocess, tid): tid for tid in test_ids}
            for future in as_completed(futures):
                tid = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "id": tid, "passou": False, "tempo_s": 0,
                        "scores": {}, "erro": str(exc),
                        "resposta_preview": "", "resposta_completa": "",
                    }
                completados[tid] = result
                icon = "[OK]" if result.get("passou") else "[FALHOU]"
                elapsed = result.get("tempo_s", "?")
                print(f"  {icon} [{tid}] concluido em {elapsed}s")
                # Salva em ordem original a cada conclusão
                resultados = [completados[t] for t in test_ids if t in completados]
                salvar_resultados(resultados)

    # ─── Resumo final ──────────────────────────────────────────────────────────
    tempo_total = round(time.perf_counter() - t_inicio, 2)
    passou_count = sum(1 for r in resultados if r.get("passou"))
    total = len(resultados)

    print(f"\n{'='*64}")
    print(f"  RESUMO FINAL -- gpt-5-mini")
    print(f"{'='*64}")
    for r in resultados:
        icon = "[OK]" if r.get("passou") else "[FALHOU]"
        descricao = r.get("descricao", r.get("id", ""))
        print(f"  {icon} [{r['id']}] {descricao} -- {r.get('tempo_s', '?')}s")

    print(f"\n  Score:       {passou_count}/{total}")
    print(f"  Tempo muro:  {tempo_total:.1f}s  (todos os processos)")
    print(f"  Salvo em:    {_OUT_FILE}")
    print(f"{'='*64}\n")


if __name__ == "__main__":
    main()
