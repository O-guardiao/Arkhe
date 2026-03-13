"""
Executa UM único teste e imprime o resultado como JSON na última linha do stdout.
Cada chamada roda em seu próprio processo Python isolado.

Uso:
    py -3.13 fine_tuning/run_single_test.py T01_codigo_basico
    py -3.13 fine_tuning/run_single_test.py T05_multistep
"""
import json
import os
import sys
from pathlib import Path

# Garante que o fork RLM está no sys.path
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from fine_tuning.test_baseline_gpt5mini import TEST_CASES, executar_teste

if len(sys.argv) < 2:
    print(json.dumps({"erro": "uso: run_single_test.py <TEST_ID>"}))
    os._exit(1)

test_id = sys.argv[1]
tc = next((t for t in TEST_CASES if t["id"] == test_id), None)

if tc is None:
    print(json.dumps({"erro": f"teste '{test_id}' não encontrado"}))
    os._exit(1)

result = executar_teste(tc)

# Última linha = JSON puro (orquestrador lê só a última linha)
print("__RESULT__:" + json.dumps(result, ensure_ascii=False))

# Força saída sem esperar threads do ThreadPoolExecutor (não-daemon)
os._exit(0)
