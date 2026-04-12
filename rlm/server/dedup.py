"""
Shim de backward compat — canônico em ``rlm.core.comms.dedup``.

.. deprecated:: O módulo mudou para ``rlm.core.comms.dedup``.
   Este re-export será removido em uma versão futura.
"""
from rlm.core.comms.dedup import IDisposable, MessageDedup  # noqa: F401

__all__ = ["IDisposable", "MessageDedup"]
