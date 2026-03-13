"""
Plugin de Áudio — Phase 11.3 (STT + TTS)

Funcionalidades:
  1. STT  — Whisper: transcribe_audio(source) -> str
  2. TTS  — OpenAI Speech: synthesize_speech(text, voice) -> bytes
  3. REPL — set_tts_voice(voice): configura voz padrão
  4. Adapter — AudioAdapter registrado em ChannelRegistry com prefixo "audio"

Dependência runtime: openai >= 1.0.0 (lazy import — falha graciosa se ausente).
Integra com EventRouter via transcribe_from_payload(payload) para STT automático.
"""

import base64
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Union

from rlm.core.structured_log import get_logger

log = get_logger("audio")


# ---------------------------------------------------------------------------
# Plugin Manifest
# ---------------------------------------------------------------------------

try:
    from rlm.plugins import PluginManifest
except ImportError:
    from dataclasses import dataclass, field

    @dataclass
    class PluginManifest:  # type: ignore
        name: str = ""
        version: str = ""
        description: str = ""
        functions: list = field(default_factory=list)
        author: str = ""
        requires: list = field(default_factory=list)


MANIFEST = PluginManifest(
    name="audio",
    version="1.0.0",
    description="STT (Whisper) + TTS (OpenAI Speech) para canais de voz.",
    functions=["transcribe_audio", "synthesize_speech", "set_tts_voice"],
    author="RLM Engine",
    requires=["openai>=1.0.0"],
)


# ---------------------------------------------------------------------------
# Configuração de voz TTS
# ---------------------------------------------------------------------------

_DEFAULT_TTS_VOICE: str = "alloy"
_TTS_VOICES: tuple[str, ...] = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
# Modelos suportados
_STT_MODEL = "whisper-1"
_TTS_MODEL = "tts-1"


def set_tts_voice(voice: str) -> str:
    """
    Configura a voz padrão de TTS para este processo.

    Args:
        voice: Um de: alloy, echo, fable, onyx, nova, shimmer.

    Returns:
        Mensagem de confirmação ou erro.
    """
    global _DEFAULT_TTS_VOICE
    if voice not in _TTS_VOICES:
        return f"Voz inválida: '{voice}'. Opções: {', '.join(_TTS_VOICES)}"
    _DEFAULT_TTS_VOICE = voice
    return f"Voz TTS configurada para '{voice}'"


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _get_openai_client():
    """Retorna um cliente OpenAI síncrono. Falha graciosamente se não instalado."""
    try:
        import openai  # noqa: F401 — lazy import
    except ImportError:
        raise ImportError(
            "O pacote 'openai' não está instalado.\n"
            "Instale com: pip install 'openai>=1.0.0'"
        )
    return openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


def _source_to_tempfile(source: Union[str, bytes, Path]) -> tuple[str, bool]:
    """
    Normaliza source para um caminho de arquivo em disco.

    Returns:
        (file_path, is_temp): se is_temp=True, o chamador deve deletar o arquivo.
    """
    if isinstance(source, bytes):
        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="rlm_stt_")
        os.close(fd)
        with open(tmp, "wb") as f:
            f.write(source)
        return tmp, True

    source_str = str(source)

    # URL remota
    if source_str.startswith(("http://", "https://")):
        ext = os.path.splitext(source_str.split("?")[0])[-1] or ".mp3"
        if len(ext) > 5:      # URL sem extensão clara
            ext = ".mp3"
        fd, tmp = tempfile.mkstemp(suffix=ext, prefix="rlm_stt_")
        os.close(fd)
        try:
            urllib.request.urlretrieve(source_str, tmp)
        except Exception as e:
            os.unlink(tmp)
            raise ValueError(f"Falha ao baixar áudio de '{source_str[:80]}': {e}") from e
        return tmp, True

    # Base64 com header "data:audio/...;base64,"
    if "base64," in source_str:
        b64_data = source_str.split("base64,", 1)[1]
        raw = base64.b64decode(b64_data)
        fd, tmp = tempfile.mkstemp(suffix=".mp3", prefix="rlm_stt_")
        os.close(fd)
        with open(tmp, "wb") as f:
            f.write(raw)
        return tmp, True

    # Base64 puro (string sem caminho/URL/header) — tenta decodificar
    # Heurística: comprimento razoável, apenas chars base64 válidos
    _B64_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n")
    if len(source_str) >= 8 and all(c in _B64_CHARS for c in source_str.strip()):
        try:
            raw = base64.b64decode(source_str.strip())
            fd, tmp = tempfile.mkstemp(suffix=".mp3", prefix="rlm_stt_")
            os.close(fd)
            with open(tmp, "wb") as f:
                f.write(raw)
            return tmp, True
        except Exception:
            pass  # Não era base64 válido; cai no erro abaixo

    # Arquivo local
    if os.path.exists(source_str):
        return source_str, False

    raise ValueError(
        f"Fonte de áudio não reconhecida ou arquivo não encontrado: '{source_str[:120]}'"
    )


# ---------------------------------------------------------------------------
# STT — Whisper
# ---------------------------------------------------------------------------

def transcribe_audio(
    source: Union[str, bytes, Path],
    model: str = _STT_MODEL,
    language: str | None = None,
) -> str:
    """
    Transcreve áudio usando OpenAI Whisper.

    Args:
        source: Aceita:
          - bytes           — áudio raw (salvo como .wav automaticamente)
          - str URL         — http/https:// — faz download para temp
          - str base64      — "data:audio/...;base64,..." ou base64 puro
          - str/Path local  — caminho de arquivo existente em disco
        model: Modelo Whisper. Default: "whisper-1".
        language: Código ISO 639-1 opcional (ex: "pt", "en"). None = auto-detect.

    Returns:
        Texto transcrito.

    Raises:
        ImportError: Se openai >= 1.0.0 não está instalado.
        ValueError: Se a fonte não é reconhecida ou inacessível.
    """
    client = _get_openai_client()
    audio_path, is_temp = _source_to_tempfile(source)

    try:
        kwargs: dict = {}
        if language:
            kwargs["language"] = language

        with open(audio_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model=model,
                file=f,
                **kwargs,
            )
        return response.text

    finally:
        if is_temp and os.path.exists(audio_path):
            try:
                os.unlink(audio_path)
            except OSError:
                pass


def transcribe_from_payload(payload: dict, model: str = _STT_MODEL) -> str:
    """
    Despacha STT a partir dos campos de um payload de webhook.

    Inspeciona em ordem de prioridade:
      1. payload["audio_bytes"]   — bytes raw
      2. payload["audio_base64"]  — string base64
      3. payload["audio_url"]     — URL HTTP/HTTPS
      4. payload["file_path"]     — caminho local

    Args:
        payload: Dicionário do webhook.
        model: Modelo Whisper.

    Returns:
        Texto transcrito.

    Raises:
        ValueError: Se nenhuma fonte de áudio é encontrada.
    """
    for field_name in ("audio_bytes", "audio_base64", "audio_url", "file_path"):
        if field_name in payload:
            return transcribe_audio(payload[field_name], model=model)

    raise ValueError(
        "Nenhuma fonte de áudio encontrada no payload. "
        "Campos aceitos: audio_bytes, audio_base64, audio_url, file_path."
    )


# ---------------------------------------------------------------------------
# TTS — OpenAI Speech
# ---------------------------------------------------------------------------

def synthesize_speech(
    text: str,
    voice: str | None = None,
    output_format: str = "mp3",
    output_path: str | None = None,
    model: str | None = None,
) -> bytes:
    """
    Sintetiza fala usando OpenAI TTS.

    Args:
        text: Texto para sintetizar (máx ~4 096 chars por chamada).
        voice: Um de: alloy, echo, fable, onyx, nova, shimmer. None = voz padrão.
        output_format: "mp3" (default), "opus", "aac" ou "flac".
        output_path: Se fornecido, salva o áudio neste caminho E retorna os bytes.
        model: Modelo TTS. Default: _TTS_MODEL ("tts-1"). Use "tts-1-hd" para alta fidelidade.

    Returns:
        Bytes do áudio gerado.

    Raises:
        ImportError: Se openai >= 1.0.0 não está instalado.
    """
    client = _get_openai_client()
    chosen_voice = voice or _DEFAULT_TTS_VOICE
    chosen_model = model or _TTS_MODEL

    response = client.audio.speech.create(
        model=chosen_model,
        voice=chosen_voice,
        input=text,
        response_format=output_format,
    )
    audio_bytes: bytes = response.content

    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(audio_bytes)
        log.info(f"TTS salvo em: {output_path} ({len(audio_bytes)} bytes)")

    return audio_bytes


# ---------------------------------------------------------------------------
# ChannelAdapter — prefixo "audio:"
# ---------------------------------------------------------------------------

try:
    from rlm.plugins.channel_registry import ChannelAdapter, ChannelRegistry

    class AudioAdapter(ChannelAdapter):
        """
        Adapter para canais de áudio puro (ex: audio:session_001).

        send_message(): sintetiza TTS → salva mp3 em tts_output_dir.
          Em produção, sobrescreva ou estenda para entregar via S3, fila, etc.
        send_media(): loga o caminho do áudio gerado externamente.

        Para integração com canais concretos (Telegram, WhatsApp),
        use o TelegramAdapter em telegram.py que chama `send_audio()`.
        """

        def __init__(self, tts_output_dir: str | None = None):
            self.tts_output_dir = tts_output_dir or tempfile.gettempdir()

        def send_message(self, target_id: str, text: str) -> bool:
            """Sintetiza TTS e salva como mp3. Retorna True se gerado com sucesso."""
            try:
                fname = f"tts_{target_id}_{os.getpid()}.mp3"
                out_path = os.path.join(self.tts_output_dir, fname)
                synthesize_speech(text, output_path=out_path)
                log.info(f"TTS gerado para '{target_id}': {out_path}")
                return True
            except Exception as e:
                log.error(f"Falha TTS para '{target_id}': {e}")
                return False

        def send_media(self, target_id: str, media_url_or_path: str, caption: str = "") -> bool:
            """
            Registra entrega de mídia de áudio.
            Sobrescreva para enviar via canal de entrega real (S3, broker, etc).
            """
            log.info(f"send_media audio → '{target_id}': {media_url_or_path}")
            return True

    # Auto-registro com prefixo "audio"
    ChannelRegistry.register("audio", AudioAdapter())

except ImportError:
    pass  # Permite uso standalone sem o registry
