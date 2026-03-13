"""
RLM Event Router — Fase 7.5

Roteia webhooks recebidos para o prompt correto com os plugins corretos.
Define regras de roteamento: qual source carrega quais plugins e qual
template de prompt usar.
"""
import re
from dataclasses import dataclass, field

from rlm.logging import get_runtime_logger


log = get_runtime_logger("event_router")


# ---------------------------------------------------------------------------
# Route Definition
# ---------------------------------------------------------------------------

@dataclass
class EventRoute:
    """Define como rotear um evento para o RLM."""
    source_pattern: str          # Glob pattern (ex: "telegram:*", "github:*")
    plugins: list[str] = field(default_factory=list)  # Plugins to load
    prompt_template: str = ""    # Template with {payload} placeholder
    description: str = ""


# ---------------------------------------------------------------------------
# Default Routes
# ---------------------------------------------------------------------------

DEFAULT_ROUTES: list[EventRoute] = [
    # ── Phase 11.2: Multimodal routes (higher priority — checked first) ──────
    EventRoute(
        source_pattern="vision:*",
        plugins=[],
        prompt_template="{message}",
        description=(
            "Entrada visual: passa lista de content parts (text + image_url) diretamente "
            "ao LLM de visão (ex: gpt-4o). O payload deve ter chave 'message' como lista "
            "de dicts no formato OpenAI content parts."
        ),
    ),
    EventRoute(
        source_pattern="audio:*",
        plugins=[],
        prompt_template=(
            "ENTRADA DE ÁUDIO:\n"
            "Transcrição: {transcription}\n"
            "URL do áudio: {audio_url}\n\n"
            "Analise este conteúdo de áudio e responda de forma apropriada. "
            "Use o `context` no REPL para acessar a transcrição se necessário."
        ),
        description=(
            "Entrada de áudio: transcrição STT já processada + URL do arquivo original. "
            "Para transcrição automática (Whisper), pré-processe via EventRouter antes do roteamento."
        ),
    ),
    # ── Chat / messaging channels ────────────────────────────────────────────
    EventRoute(
        source_pattern="telegram:*",
        plugins=["telegram"],
        prompt_template=(
            "USER MESSAGE (TELEGRAM):\n"
            "From: {from_user}\n"
            "Message: {text}\n\n"
            "Respond using the universal `reply(text)` tool. "
            "Analyze the message and act appropriately."
        ),
        description="Route Telegram messages to RLM using Universal Channel response",
    ),
    EventRoute(
        source_pattern="webhook:*",
        plugins=[],
        prompt_template=(
            "WEBHOOK EVENT RECEIVED:\n"
            "Source: {source}\n"
            "```json\n{payload_json}\n```\n\n"
            "Analyze this event and take necessary actions. "
            "Return a concise summary of what was done."
        ),
        description="Generic webhook handler",
    ),
    EventRoute(
        source_pattern="cron:*",
        plugins=[],
        prompt_template=(
            "SCHEDULED TASK:\n"
            "Task: {task_name}\n"
            "Schedule: {schedule}\n\n"
            "Execute the scheduled task described above."
        ),
        description="Scheduled/cron task execution",
    ),
    # ── Discord Interactions ─────────────────────────────────────────────────
    EventRoute(
        source_pattern="discord:*",
        plugins=["discord"],
        prompt_template=(
            "DISCORD MESSAGE:\n"
            "From: {username} (user_id: {user_id})\n"
            "Guild: {guild_id} | Channel: {channel_id}\n"
            "Message: {text}\n\n"
            "Respond using `from rlm.plugins.discord import send_webhook` "
            "ou `send_channel_message(channel_id, text)` para responder ao usuário. "
            "Analise a mensagem e execute a ação solicitada."
        ),
        description="Discord Interactions — slash commands e botões",
    ),
    # ── WhatsApp (Meta Cloud API) ─────────────────────────────────────────────
    EventRoute(
        source_pattern="whatsapp:*",
        plugins=["whatsapp"],
        prompt_template=(
            "WHATSAPP MESSAGE:\n"
            "From: {from_user} (wa_id: {wa_id})\n"
            "Type: {type}\n"
            "Message: {text}\n\n"
            "Respond using `from rlm.plugins.whatsapp import send_text` "
            "para responder: send_text('{wa_id}', 'sua resposta'). "
            "Analise a mensagem e responda de forma clara e direta. "
            "Para mídias recebidas, use get_media_url(media_id) se precisar processá-las."
        ),
        description="WhatsApp Business via Meta Cloud API",
    ),
    # ── Slack Events API ─────────────────────────────────────────────────────
    EventRoute(
        source_pattern="slack:*",
        plugins=["slack"],
        prompt_template=(
            "SLACK MESSAGE:\n"
            "From: {from_user} | Channel: {channel} | Team: {team_id}\n"
            "Event: {event_type}\n"
            "Message: {text}\n\n"
            "Respond using `from rlm.plugins.slack import post_message` "
            "para responder: post_message('{channel}', 'sua resposta'). "
            "Se a mensagem veio de um thread, use post_reply('{channel}', '{thread_ts}', texto). "
            "Analise a solicitação e execute a ação adequada."
        ),
        description="Slack Events API — app_mention e DMs",
    ),
    # ── WebChat SSE ──────────────────────────────────────────────────────────
    EventRoute(
        source_pattern="webchat:*",
        plugins=[],
        prompt_template=(
            "WEB CHAT MESSAGE:\n"
            "From: {from_user} (session: {session_id})\n"
            "Message: {text}\n\n"
            "Responda de forma clara, usando markdown quando útil. "
            "Esta mensagem vem do WebChat do RLM — resposta vai direto ao navegador via SSE."
        ),
        description="WebChat SSE — interface web do RLM",
    ),
]


# ---------------------------------------------------------------------------
# Event Router
# ---------------------------------------------------------------------------

class EventRouter:
    """
    Routes incoming events to the appropriate RLM prompt and plugins.
    
    Usage:
        router = EventRouter()
        prompt, plugins = router.route("telegram:12345", {
            "chat_id": 12345,
            "from_user": "João",
            "text": "Deploy the frontend"
        })
    """

    def __init__(self, routes: list[EventRoute] | None = None):
        self.routes = routes or DEFAULT_ROUTES.copy()

    def add_route(self, route: EventRoute):
        """Add a new route."""
        self.routes.insert(0, route)  # Higher priority for custom routes

    def route(self, client_id: str, payload: dict) -> tuple[str | list, list[str]]:
        """
        Find the best matching route and format the prompt.
        
        Args:
            client_id: The source identifier (ex: "telegram:12345")
            payload: The event payload dictionary
            
        Returns:
            Tuple of (formatted_prompt, list_of_plugins_to_load)
        """
        for event_route in self.routes:
            if self._matches(event_route.source_pattern, client_id):
                prompt = self._format_prompt(event_route.prompt_template, client_id, payload)
                return prompt, event_route.plugins

        # Fallback: generic prompt, no plugins
        import json
        fallback_prompt = (
            f"EVENT from {client_id}:\n"
            f"```json\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n```\n\n"
            "Analyze this event and respond with a summary of actions taken."
        )
        return fallback_prompt, []

    def list_routes(self) -> list[dict]:
        """List all configured routes as dicts (for API responses)."""
        return [
            {
                "source_pattern": r.source_pattern,
                "plugins": r.plugins,
                "description": r.description,
            }
            for r in self.routes
        ]

    # --- Internal ---

    @staticmethod
    def _matches(pattern: str, client_id: str) -> bool:
        """Simple glob matching: 'telegram:*' matches 'telegram:12345'."""
        # Escape regex metacharacters, then convert glob wildcards
        regex = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
        return bool(re.fullmatch(regex, client_id, re.IGNORECASE))

    @staticmethod
    def _format_prompt(template: str, client_id: str, payload: dict) -> str | list:
        """Format a prompt template with payload data."""
        if not template:
            import json
            return f"Event from {client_id}: {json.dumps(payload, ensure_ascii=False)}"

        # Build replacement dict from payload + extras
        import json
        replacements = {
            "source": client_id,
            "payload_json": json.dumps(payload, indent=2, ensure_ascii=False),
            **{k: str(v) for k, v in payload.items()},
        }

        # Multimodal pass-through (if template is exactly {text} or {message})
        if template.strip() == "{text}" and "text" in payload:
            return payload["text"]
        elif template.strip() == "{message}" and "message" in payload:
            return payload["message"]

        # Safe format: replace {key} but don't fail on missing keys
        result = template
        for key, value in replacements.items():
            if not isinstance(value, str):
                import json
                value = json.dumps(value, ensure_ascii=False)
            result = result.replace(f"{{{key}}}", value)

        return result

    @staticmethod
    def preprocess_audio(client_id: str, payload: dict) -> dict:
        """
        Pré-processa payloads de canais de áudio (audio:*) via STT (Whisper).

        Se o payload ainda não contém 'transcription', tenta transcrever o áudio
        presente em um dos campos: audio_bytes, audio_base64, audio_url, file_path.

        Chamado pelo webhook ANTES de route(), de modo que o template
        audio:* pode usar {transcription} normalmente.

        Args:
            client_id: Ex: "audio:mic_01" — só processa prefixo "audio:".
            payload: Payload bruto do webhook.

        Returns:
            Payload enriquecido (com 'transcription') ou original inalterado.
        """
        if not client_id.startswith("audio:"):
            return payload

        if "transcription" in payload:
            # Já pré-processado ou enviado diretamente pelo cliente
            return payload

        audio_fields = ("audio_bytes", "audio_base64", "audio_url", "file_path")
        has_audio = any(payload.get(f) for f in audio_fields)
        if not has_audio:
            return payload

        try:
            from rlm.plugins.audio import transcribe_from_payload

            transcription = transcribe_from_payload(payload)
            enriched = dict(payload)
            enriched["transcription"] = transcription

            # Mantém audio_url disponível para o template, se presente
            if "audio_url" not in enriched:
                enriched["audio_url"] = ""

            return enriched

        except ImportError:
            log.warn(
                "Audio plugin não disponível para preprocess_audio",
                client_id=client_id,
                recommendation="Instale openai>=1.0.0 para habilitar STT",
            )
        except Exception as exc:
            log.error(
                "Transcrição falhou em preprocess_audio",
                client_id=client_id,
                error=str(exc),
            )

        # Fallback: retorna payload sem transcrição; o template receberá {transcription} vazio
        fallback = dict(payload)
        fallback.setdefault("transcription", "")
        fallback.setdefault("audio_url", "")
        return fallback
