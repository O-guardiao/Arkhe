from typing import Any

# Case-insensitive substrings that mark a kwarg value as sensitive.
# Checked against the lowercased key name.
_SENSITIVE_KEY_SUBSTRINGS = (
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "token",
    "bearer",
    "authorization",
    "credential",
    "private_key",
    "access_key",
)


def filter_sensitive_keys(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Filter out sensitive keys (credentials, tokens, secrets) from kwargs.

    Used before logging or persisting kwargs to avoid leaking secrets.
    Matches known sensitive substrings against the lowercased key name.
    """
    filtered = {}
    for key, value in kwargs.items():
        key_lower = key.lower()
        if any(substr in key_lower for substr in _SENSITIVE_KEY_SUBSTRINGS):
            continue
        filtered[key] = value
    return filtered
