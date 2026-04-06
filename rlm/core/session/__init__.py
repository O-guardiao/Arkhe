"""Session management: session lifecycle, client registry."""

# Re-export from _impl.py (was session.py) so que
# ``from rlm.core.session import SessionManager`` continue funcionando.
from rlm.core.session._impl import *  # noqa: F401,F403

# Utilitários portados de packages/sessions/ (TypeScript → Python)
from rlm.core.session.session_key import (  # noqa: F401
    SessionId,
    SessionKey,
    create_session_id,
    is_session_id,
    make_session_id,
    encode_session_key,
    decode_session_key,
    session_key_hash,
)
from rlm.core.session.session_label import (  # noqa: F401
    generate_label,
    sanitize_label,
    format_session_summary,
)
from rlm.core.session.transcript import (  # noqa: F401
    TranscriptEventType,
    TranscriptEvent,
    create_transcript_event,
    TranscriptStore,
    is_valid_event_type,
)
from rlm.core.session.model_overrides import (  # noqa: F401
    ModelOverride,
    ModelOverrideMap,
)
from rlm.core.session.send_policy import (  # noqa: F401
    SendPolicy,
    DEFAULT_SEND_POLICY,
    PolicyCheckResult,
    check_send_policy,
    RateLimiter,
)
