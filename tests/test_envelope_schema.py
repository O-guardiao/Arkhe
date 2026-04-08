from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from rlm.gateway.envelope import Envelope, create_envelope, validate_envelope_payload


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "schemas" / "envelope.v1.json"


def _load_schema() -> dict[str, Any]:
    loaded = json.loads(_schema_path().read_text(encoding="utf-8"))
    return cast(dict[str, Any], loaded)


def _schema_example() -> dict[str, Any]:
    schema = _load_schema()
    examples = schema.get("examples") or []
    assert examples, "schema precisa expor ao menos um exemplo canônico"
    return cast(dict[str, Any], dict(examples[0]))


class TestEnvelopeSchemaContract:
    def test_schema_example_roundtrip(self) -> None:
        example = _schema_example()

        envelope = Envelope.from_dict(example)

        assert envelope.to_dict() == example

    def test_factory_output_is_schema_valid(self) -> None:
        envelope = create_envelope(
            "telegram",
            "123456789",
            "Olá, preciso de ajuda com Python",
            metadata={"chat_id": 123456789, "message_id": 42},
        )

        validate_envelope_payload(envelope.to_dict())

    def test_from_dict_rejects_additional_properties(self) -> None:
        payload = _schema_example()
        payload["unexpected"] = True

        with pytest.raises(ValueError, match="[Aa]dditional properties"):
            Envelope.from_dict(payload)

    def test_from_dict_rejects_invalid_source_client_id(self) -> None:
        payload = _schema_example()
        payload["source_client_id"] = "telegram_123456789"

        with pytest.raises(ValueError, match="source_client_id"):
            Envelope.from_dict(payload)