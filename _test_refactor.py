"""Testes críticos pós-refatoração de rlm.py"""
from unittest.mock import MagicMock, patch
from rlm.core.engine.rlm import RLM
from rlm.core.engine.rlm_context_mixin import RLMContextMixin
from rlm.core.engine.rlm_loop_mixin import RLMLoopMixin
from rlm.core.engine.rlm_mcts_mixin import RLMMCTSMixin
from rlm.core.engine.rlm_persistence_mixin import RLMPersistenceMixin

PASS = []
FAIL = []

def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  PASS  {name}")
    except Exception as e:
        FAIL.append((name, e))
        print(f"  FAIL  {name}: {e}")

# ── 1. Herança ────────────────────────────────────────────────────────────────
def t_heranca():
    assert issubclass(RLM, RLMContextMixin)
    assert issubclass(RLM, RLMLoopMixin)
    assert issubclass(RLM, RLMMCTSMixin)
    assert issubclass(RLM, RLMPersistenceMixin)

# ── 2. MRO correto ────────────────────────────────────────────────────────────
def t_mro():
    mro_names = [c.__name__ for c in RLM.__mro__]
    expected_order = ['RLM', 'RLMContextMixin', 'RLMLoopMixin', 'RLMMCTSMixin', 'RLMPersistenceMixin']
    for i, name in enumerate(expected_order):
        assert mro_names.index(name) == i, f"{name} na posicao errada no MRO"

# ── 3. Métodos públicos presentes ─────────────────────────────────────────────
def t_metodos_publicos():
    required = [
        'completion', 'completion_stream', 'sentinel_completion',
        'save_state', 'resume_state', 'close', 'dispose',
        'shutdown_persistent', '__enter__', '__exit__',
    ]
    for m in required:
        assert hasattr(RLM, m), f"Metodo ausente: {m}"

# ── 4. Métodos privados nos mixins corretos ───────────────────────────────────
def t_ownership():
    def owner(method_name):
        for cls in RLM.__mro__:
            if method_name in cls.__dict__:
                return cls.__name__
        return None

    cases = {
        'completion':                 'RLM',
        'completion_stream':          'RLM',
        'sentinel_completion':        'RLM',
        '_spawn_completion_context':  'RLMContextMixin',
        '_setup_prompt':              'RLMContextMixin',
        '_inject_repl_globals':       'RLMContextMixin',
        '_run_inner_loop':            'RLMLoopMixin',
        '_completion_turn':           'RLMLoopMixin',
        '_fallback_answer':           'RLMLoopMixin',
        '_default_answer':            'RLMLoopMixin',
        '_run_mcts_preamble':         'RLMMCTSMixin',
        '_build_mcts_evaluation_stages': 'RLMMCTSMixin',
        'save_state':                 'RLMPersistenceMixin',
        'resume_state':               'RLMPersistenceMixin',
        'shutdown_persistent':        'RLMPersistenceMixin',
    }
    for method, expected_cls in cases.items():
        actual = owner(method)
        assert actual == expected_cls, f"{method}: esperado em {expected_cls}, encontrado em {actual}"

# ── 5. Assinaturas das APIs públicas intactas ─────────────────────────────────
def t_assinaturas():
    import inspect
    sig = inspect.signature(RLM.completion)
    params = list(sig.parameters.keys())
    assert params == ['self', 'prompt', 'root_prompt', 'mcts_branches', 'capture_artifacts'], \
        f"completion() assinatura alterada: {params}"

    sig2 = inspect.signature(RLM.completion_stream)
    params2 = list(sig2.parameters.keys())
    assert params2 == ['self', 'prompt', 'root_prompt', 'mcts_branches'], \
        f"completion_stream() assinatura alterada: {params2}"

    sig3 = inspect.signature(RLM.sentinel_completion)
    params3 = list(sig3.parameters.keys())
    assert params3 == ['self', 'prompt', 'root_prompt'], \
        f"sentinel_completion() assinatura alterada: {params3}"

# ── 6. Instanciação (__init__) ────────────────────────────────────────────────
def t_init():
    rlm = RLM(backend='openai', backend_kwargs={'model_name': 'gpt-4o'}, max_iterations=3)
    assert rlm.backend == 'openai'
    assert rlm.max_iterations == 3
    assert rlm.depth == 0
    assert rlm.persistent is False
    assert hasattr(rlm, '_cancel_token')
    assert hasattr(rlm, 'hooks')
    assert hasattr(rlm, 'compactor')
    assert hasattr(rlm, 'loop_detector')
    assert hasattr(rlm, '_sentinel_input_queue')
    assert hasattr(rlm, '_sentinel_output_queue')
    assert rlm._persistent_lm_handler is None
    assert rlm._persistent_env is None

# ── 7. Protocolo with ─────────────────────────────────────────────────────────
def t_context_manager():
    rlm = RLM(backend='openai', backend_kwargs={'model_name': 'gpt-4o'})
    assert hasattr(rlm, '__enter__') and callable(rlm.__enter__)
    assert hasattr(rlm, '__exit__') and callable(rlm.__exit__)
    result = rlm.__enter__()
    assert result is rlm, "__enter__ deve retornar self"

# ── 8. Nenhum método duplicado entre mixins ───────────────────────────────────
def t_sem_duplicatas():
    method_map = {}
    for cls in [RLMContextMixin, RLMLoopMixin, RLMMCTSMixin, RLMPersistenceMixin]:
        for name in cls.__dict__:
            if not name.startswith('__'):
                if name in method_map:
                    raise AssertionError(f"Metodo '{name}' duplicado em {method_map[name]} e {cls.__name__}")
                method_map[name] = cls.__name__

# ── Execução ──────────────────────────────────────────────────────────────────
print("\n=== TESTES CRITICOS POS-REFATORACAO ===\n")

check("1. Heranca dos 4 mixins", t_heranca)
check("2. MRO correto", t_mro)
check("3. Metodos publicos presentes", t_metodos_publicos)
check("4. Metodos nos mixins corretos (ownership)", t_ownership)
check("5. Assinaturas das APIs publicas intactas", t_assinaturas)
check("6. Instanciacao __init__ completa", t_init)
check("7. Protocolo 'with' (__enter__/__exit__)", t_context_manager)
check("8. Sem metodos duplicados entre mixins", t_sem_duplicatas)

print(f"\n{'='*40}")
print(f"RESULTADO: {len(PASS)} PASSOU  |  {len(FAIL)} FALHOU")
if FAIL:
    print("\nFALHAS:")
    for name, err in FAIL:
        print(f"  - {name}: {err}")
print('='*40)
