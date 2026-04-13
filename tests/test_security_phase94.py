"""
Tests for Phase 9.4 (CiberSeg) security hardening.

Covers:
- _safe_open(): path restriction in REPL sandbox
- JWT: issue_token / verify_token (stdlib, zero deps)
- Client Registry: register, authenticate, deactivate, rotate
- Audit Log: event recording and retrieval
- Rate Limiter: dual-key (IP + client_id)
"""
import os
import time
import tempfile
import sqlite3
import pytest


# ===========================================================================
# _safe_open tests
# ===========================================================================


class TestSafeOpen:
    """Test the sandboxed open() wrapper in local_repl."""

    def test_safe_open_blocks_dotenv(self):
        """_safe_open must reject .env files."""
        from rlm.environments.local_repl import _safe_open

        with pytest.raises(PermissionError, match=r"\.env.*blocked"):
            _safe_open(".env", "r")

    def test_safe_open_blocks_dotenv_variants(self):
        """_safe_open must reject .env.local, .env.production, etc."""
        from rlm.environments.local_repl import _safe_open

        for name in [".env.local", ".env.production", ".ENV"]:
            with pytest.raises(PermissionError, match=r"blocked"):
                _safe_open(name, "r")

    def test_safe_open_blocks_ssh_dir(self):
        """_safe_open must reject files under ~/.ssh/."""
        from rlm.environments.local_repl import _safe_open
        from rlm.core.security import SecurityViolation

        ssh_path = os.path.join(os.path.expanduser("~"), ".ssh", "id_rsa")
        with pytest.raises(SecurityViolation, match="Path access denied"):
            _safe_open(ssh_path, "r")

    def test_safe_open_blocks_aws_credentials(self):
        """_safe_open must reject ~/.aws paths."""
        from rlm.environments.local_repl import _safe_open
        from rlm.core.security import SecurityViolation

        aws_path = os.path.join(os.path.expanduser("~"), ".aws", "credentials")
        with pytest.raises(SecurityViolation, match="Path access denied"):
            _safe_open(aws_path, "r")

    def test_safe_open_allows_normal_file(self, tmp_path):
        """_safe_open must allow reading a normal temp file."""
        from rlm.environments.local_repl import _safe_open

        p = tmp_path / "hello.txt"
        p.write_text("world")
        f = _safe_open(str(p), "r")
        assert f.read() == "world"
        f.close()

    def test_safe_open_in_builtins(self):
        """_SAFE_BUILTINS['open'] must be _safe_open, not the bare builtin."""
        from rlm.environments.local_repl import _SAFE_BUILTINS, _safe_open

        assert _SAFE_BUILTINS["open"] is _safe_open


# ===========================================================================
# JWT tests
# ===========================================================================


class TestJWT:
    """Test stdlib-only JWT implementation."""

    @pytest.fixture(autouse=True)
    def _set_secret(self, monkeypatch):
        monkeypatch.setenv(
            "RLM_JWT_SECRET",
            "test-secret-that-is-at-least-32-characters-long-ok",
        )

    def test_issue_and_verify(self):
        from rlm.core.security.auth import issue_token, verify_token

        token = issue_token("device_01", profile="default")
        payload = verify_token(token)
        assert payload is not None
        assert payload["sub"] == "device_01"
        assert payload["prf"] == "default"
        assert "exp" in payload
        assert "iat" in payload

    def test_expired_token_rejected(self):
        from rlm.core.security.auth import issue_token, verify_token

        token = issue_token("device_x", ttl_hours=-1)  # already expired
        assert verify_token(token) is None

    def test_tampered_token_rejected(self):
        from rlm.core.security.auth import issue_token, verify_token

        token = issue_token("device_02")
        # Flip a character in the signature
        parts = token.split(".")
        sig = list(parts[2])
        sig[0] = "A" if sig[0] != "A" else "B"
        parts[2] = "".join(sig)
        tampered = ".".join(parts)
        assert verify_token(tampered) is None

    def test_wrong_secret_rejected(self, monkeypatch):
        from rlm.core.security.auth import issue_token, verify_token

        token = issue_token("device_03")
        monkeypatch.setenv(
            "RLM_JWT_SECRET",
            "a-completely-different-secret-that-is-long-enough",
        )
        assert verify_token(token) is None

    def test_short_secret_raises(self, monkeypatch):
        monkeypatch.setenv("RLM_JWT_SECRET", "short")
        from rlm.core.security.auth import issue_token

        with pytest.raises(RuntimeError, match="at least"):
            issue_token("x")

    def test_permissions_in_payload(self):
        from rlm.core.security.auth import issue_token, verify_token

        token = issue_token("d", permissions=["read", "admin"])
        payload = verify_token(token)
        assert payload["prm"] == ["read", "admin"]

    def test_extra_claims(self):
        from rlm.core.security.auth import issue_token, verify_token

        token = issue_token("d", extra_claims={"org": "acme"})
        payload = verify_token(token)
        assert payload["org"] == "acme"

    def test_malformed_token_returns_none(self):
        from rlm.core.security.auth import verify_token

        assert verify_token("not.a.jwt.at.all") is None
        assert verify_token("") is None
        assert verify_token("onlyone") is None

    def test_alg_none_rejected(self):
        """Tokens without HS256 algorithm must be rejected."""
        import base64
        import json
        from rlm.core.security.auth import verify_token

        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps({"sub": "hack", "exp": int(time.time()) + 3600}).encode()
        ).rstrip(b"=").decode()
        fake = f"{header}.{payload}."
        assert verify_token(fake) is None

    def test_hash_token_deterministic(self):
        from rlm.core.security.auth import hash_token

        h1 = hash_token("my-secret-token")
        h2 = hash_token("my-secret-token")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex digest


# ===========================================================================
# Client Registry tests
# ===========================================================================


class TestClientRegistry:
    """Test client registration, auth, deactivation, rotation."""

    @pytest.fixture
    def registry(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "RLM_JWT_SECRET",
            "test-secret-that-is-at-least-32-characters-long-ok",
        )
        from rlm.core.session.client_registry import ClientRegistry

        db = str(tmp_path / "test_clients.db")
        return ClientRegistry(db_path=db)

    def test_register_and_authenticate(self, registry):
        client_id, raw_token = registry.register_client("esp32_sala")
        assert client_id == "esp32_sala"
        assert len(raw_token) > 0

        record = registry.authenticate(raw_token)
        assert record is not None
        assert record.id == "esp32_sala"
        assert record.profile == "default"
        assert record.active is True

    def test_duplicate_registration_raises(self, registry):
        registry.register_client("dev1")
        with pytest.raises(ValueError, match="already exists"):
            registry.register_client("dev1")

    def test_invalid_token_returns_none(self, registry):
        registry.register_client("dev2")
        assert registry.authenticate("completely-wrong-token") is None

    def test_deactivated_client_auth_fails(self, registry):
        _, raw_token = registry.register_client("dev3")
        registry.deactivate_client("dev3")
        assert registry.authenticate(raw_token) is None

    def test_reactivate_client(self, registry):
        _, raw_token = registry.register_client("dev4")
        registry.deactivate_client("dev4")
        registry.reactivate_client("dev4")
        assert registry.authenticate(raw_token) is not None

    def test_rotate_token(self, registry):
        _, old_token = registry.register_client("dev5")
        new_token = registry.rotate_token("dev5")
        assert new_token is not None
        assert new_token != old_token
        # Old token no longer works
        assert registry.authenticate(old_token) is None
        # New token works
        assert registry.authenticate(new_token) is not None

    def test_rotate_inactive_returns_none(self, registry):
        registry.register_client("dev6")
        registry.deactivate_client("dev6")
        assert registry.rotate_token("dev6") is None

    def test_list_clients(self, registry):
        registry.register_client("a")
        registry.register_client("b")
        registry.register_client("c")
        registry.deactivate_client("b")

        active = registry.list_clients(active_only=True)
        assert len(active) == 2
        all_clients = registry.list_clients(active_only=False)
        assert len(all_clients) == 3

    def test_get_client(self, registry):
        registry.register_client("lookup_test", profile="iot")
        c = registry.get_client("lookup_test")
        assert c is not None
        assert c.profile == "iot"
        assert registry.get_client("nonexistent") is None

    def test_issue_jwt_for_client(self, registry):
        from rlm.core.security.auth import verify_token

        registry.register_client("jwt_test", profile="admin")
        jwt = registry.issue_jwt("jwt_test", ttl_hours=1)
        assert jwt is not None
        payload = verify_token(jwt)
        assert payload["sub"] == "jwt_test"
        assert payload["prf"] == "admin"

    def test_issue_jwt_inactive_returns_none(self, registry):
        registry.register_client("jwt_inactive")
        registry.deactivate_client("jwt_inactive")
        assert registry.issue_jwt("jwt_inactive") is None

    def test_custom_permissions(self, registry):
        registry.register_client("perm_test", permissions=["read", "admin"])
        c = registry.get_client("perm_test")
        assert c.permissions == ["read", "admin"]


# ===========================================================================
# Audit Log tests
# ===========================================================================


class TestAuditLog:
    """Test authentication audit trail."""

    @pytest.fixture
    def registry(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "RLM_JWT_SECRET",
            "test-secret-that-is-at-least-32-characters-long-ok",
        )
        from rlm.core.session.client_registry import ClientRegistry

        db = str(tmp_path / "test_audit.db")
        return ClientRegistry(db_path=db)

    def test_registration_creates_audit_entry(self, registry):
        registry.register_client("audit_dev")
        log = registry.get_audit_log("audit_dev")
        assert len(log) >= 1
        assert log[0].event_type == "client_registered"
        assert log[0].client_id == "audit_dev"

    def test_auth_success_logged(self, registry):
        _, token = registry.register_client("auth_ok")
        registry.authenticate(token, ip_address="10.0.0.5")
        log = registry.get_audit_log("auth_ok")
        events = [e.event_type for e in log]
        assert "auth_success" in events
        # Check IP is recorded
        success = next(e for e in log if e.event_type == "auth_success")
        assert success.ip_address == "10.0.0.5"

    def test_auth_failure_logged(self, registry):
        registry.register_client("auth_fail")
        registry.authenticate("wrong_token", ip_address="192.168.1.1")
        # Auth failure is logged under "unknown" since token didn't match
        log = registry.get_audit_log("unknown")
        events = [e.event_type for e in log]
        assert "auth_failure" in events

    def test_deactivation_logged(self, registry):
        registry.register_client("deact_log")
        registry.deactivate_client("deact_log")
        log = registry.get_audit_log("deact_log")
        events = [e.event_type for e in log]
        assert "client_deactivated" in events

    def test_token_rotation_logged(self, registry):
        registry.register_client("rot_log")
        registry.rotate_token("rot_log")
        log = registry.get_audit_log("rot_log")
        events = [e.event_type for e in log]
        assert "token_rotated" in events

    def test_global_audit_log(self, registry):
        """get_audit_log without client_id returns all entries."""
        registry.register_client("g1")
        registry.register_client("g2")
        all_log = registry.get_audit_log(limit=50)
        assert len(all_log) >= 2


# ===========================================================================
# Rate Limiter dual-key tests
# ===========================================================================


class TestRateLimiterDualKey:
    """Test IP + client_id rate limiting."""

    def test_ip_rate_limit(self):
        from rlm.server.webhook_dispatch import _RateLimiter

        limiter = _RateLimiter(rpm=3)
        for _ in range(3):
            allowed, _ = limiter.is_allowed("ip:1.2.3.4")
            assert allowed is True

        allowed, retry = limiter.is_allowed("ip:1.2.3.4")
        assert allowed is False
        assert retry > 0

    def test_client_id_rate_limit(self):
        from rlm.server.webhook_dispatch import _RateLimiter

        limiter = _RateLimiter(rpm=2)
        limiter.is_allowed("cid:device_a")
        limiter.is_allowed("cid:device_a")

        allowed, _ = limiter.is_allowed("cid:device_a")
        assert allowed is False

        # Different client_id still allowed
        allowed, _ = limiter.is_allowed("cid:device_b")
        assert allowed is True

    def test_check_dual(self):
        from rlm.server.webhook_dispatch import _RateLimiter

        limiter = _RateLimiter(rpm=2)
        assert limiter.check_dual("1.1.1.1", "dev1") == (True, 0)
        assert limiter.check_dual("1.1.1.1", "dev1") == (True, 0)

        # Third call: IP exhausted
        allowed, _ = limiter.check_dual("1.1.1.1", "dev1")
        assert allowed is False

    def test_check_dual_ip_exhausted_before_cid(self):
        from rlm.server.webhook_dispatch import _RateLimiter

        limiter = _RateLimiter(rpm=2)
        # Exhaust IP with different client_ids
        limiter.check_dual("10.0.0.1", "a")
        limiter.check_dual("10.0.0.1", "b")

        # IP is exhausted even with a new client_id
        allowed, _ = limiter.check_dual("10.0.0.1", "c")
        assert allowed is False

    def test_check_dual_no_client_id(self):
        from rlm.server.webhook_dispatch import _RateLimiter

        limiter = _RateLimiter(rpm=5)
        allowed, _ = limiter.check_dual("2.2.2.2")
        assert allowed is True
