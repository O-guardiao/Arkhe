"""Optimized fast-path: parsing, wire protocol, benchmarks."""

# Re-export from _impl.py (was optimized.py) so that
# ``from rlm.core.optimized import ...`` keeps working.
from rlm.core.optimized._impl import *  # noqa: F401,F403
