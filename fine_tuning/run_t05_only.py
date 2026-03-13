"""
Executa apenas T05 e mescla o resultado ao JSON existente (T01-T04 já concluídos).
Uso:
    cd RLM_OpenClaw_Engine/rlm-main
    py -3.13 fine_tuning/run_t05_only.py
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Garante que o fork está no path
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from fine_tuning.test_baseline_gpt5mini import (
    TEST_CASES,
    RESULTS_FILE,
    executar_teste,
    MODEL,
)

# Localiza T05
t05 = next((tc for tc in TEST_CASES if tc["id"] == "T05_multistep"), None)
if t05 is None:
    print("❌ T05_multistep não encontrado em TEST_CASES")
    sys.exit(1)

# Roda T05
print(f"\n▶ Rodando T05 com o fix ThreadPoolExecutor aplicado...\n")
result = executar_teste(t05)

# Carrega resultados existentes
if RESULTS_FILE.exists():
    data = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    existing = data.get("resultados", [])
else:
    data = {"meta": {}}
    existing = []

# Remove eventual T05 anterior (re-run)
existing = [r for r in existing if r["id"] != "T05_multistep"]
existing.append(result)

passou_count = sum(1 for r in existing if r["passou"])
tempo_total  = round(sum(r["tempo_s"] for r in existing), 2)
total        = len(existing)

data["meta"].update({
    "versao_rlm": "fork_v0.1.0",
    "modelo": MODEL,
    "data": datetime.now().isoformat(),
    "score": f"{passou_count}/{total}",
    "tempo_total_s": tempo_total,
    "tipo": "baseline_sem_finetune",
})
data["resultados"] = existing

RESULTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

print(f"\n{'='*60}")
print("  RESULTADOS COMPLETOS APÓS T05")
print(f"{'='*60}")
for r in existing:
    icon = "✅" if r["passou"] else "❌"
    print(f"  {icon} [{r['id']}] {r.get('descricao','')} — {r['tempo_s']}s")
print(f"\n  Score final: {passou_count}/{total}")
print(f"  Tempo total: {tempo_total}s")
print(f"  Salvo em: {RESULTS_FILE}")
print("="*60 + "\n")

# Força saída: threads do ThreadPoolExecutor (não-daemon) bloqueiam o exit normal
os._exit(0)
