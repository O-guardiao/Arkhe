from __future__ import annotations

import os


def resolve_internal_api_base_url() -> str:
	"""Resolve a URL interna usada pelos gateways HTTP bridge.

	Precedencia:
	1. ``RLM_INTERNAL_HOST`` quando explicitamente definido;
	2. ``RLM_API_HOST`` + ``RLM_API_PORT`` como fallback operacional.

	Quando o bind externo usa ``0.0.0.0``, o cliente interno deve apontar para
	``127.0.0.1``; ``0.0.0.0`` nao e um destino de conexao confiavel.
	"""
	internal_host = os.environ.get("RLM_INTERNAL_HOST", "").strip()
	if internal_host:
		return internal_host.rstrip("/")

	host = os.environ.get("RLM_API_HOST", "127.0.0.1").strip() or "127.0.0.1"
	if host == "0.0.0.0":
		host = "127.0.0.1"

	raw_port = os.environ.get("RLM_API_PORT", "5000").strip() or "5000"
	try:
		port = int(raw_port)
	except ValueError:
		port = 5000

	if port <= 0 or port > 65535:
		port = 5000

	return f"http://{host}:{port}"