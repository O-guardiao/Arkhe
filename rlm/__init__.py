from rlm.core.rlm import RLM

__all__ = ["RLM"]


def __getattr__(name: str):
    """PEP 562 — lazy submodule access as package attribute.

    When unittest.mock.patch() resolves a dotted path like
    "rlm.server.runtime_pipeline.X", it calls _dot_lookup which does
    getattr(rlm, 'server'). If the attribute isn't set (xdist workers can
    have sys.modules entries without the parent attribute bound), Python falls
    through to this __getattr__, which imports and returns the subpackage
    on demand — silently fixing the AttributeError without eager loading.
    """
    import importlib
    try:
        return importlib.import_module(f"rlm.{name}")
    except ModuleNotFoundError:
        raise AttributeError(f"module 'rlm' has no attribute {name!r}") from None
