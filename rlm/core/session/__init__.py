"""Session management: session lifecycle, client registry."""

# Re-export from _impl.py (was session.py) so that
# ``from rlm.core.session import SessionManager`` keeps working.
from rlm.core.session._impl import *  # noqa: F401,F403
