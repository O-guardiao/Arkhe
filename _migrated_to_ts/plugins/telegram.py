"""
Plugin Telegram — rlm/plugins/telegram.py

Funções expostas ao REPL do RLM para interagir com o Telegram Bot API.
O RLM pode importar este módulo e chamar as funções diretamente no REPL:

    >>> from rlm.plugins.telegram import send_message, get_updates
    >>> send_message(12345, "Deploy concluído!")
    'Message sent to 12345'
"""
import os
import json
from io import BytesIO
from urllib import request, error, parse as url_parse
from dataclasses import dataclass, field

# Import PluginManifest for the MANIFEST constant
# This try/except allows the module to work even if imported standalone
try:
    from rlm.plugins import PluginManifest
except ImportError:
    from dataclasses import dataclass as _dc
    @_dc
    class PluginManifest:  # type: ignore
        name: str = ""
        version: str = ""
        description: str = ""
        functions: list = field(default_factory=list)
        author: str = ""
        requires: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Plugin Manifest
# ---------------------------------------------------------------------------

MANIFEST = PluginManifest(
    name="telegram",
    version="1.1.0",
    description="Telegram Bot API — mensagens, mídia (foto/áudio TTS), updates, documentos.",
    functions=[
        "send_message", "get_updates", "send_document", "reply_to", "get_chat_info",
        "send_audio", "send_photo", "download_telegram_file",
    ],
    author="RLM Engine",
    requires=[],  # Uses stdlib urllib only
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _get_token() -> str:
    """Get Telegram Bot Token from environment."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN not set. "
            "Set it with: os.environ['TELEGRAM_BOT_TOKEN'] = 'your-token'"
        )
    return token


def _api_call(method: str, params: dict | None = None) -> dict:
    """Make a Telegram Bot API call (JSON payload)."""
    token = _get_token()
    url = f"https://api.telegram.org/bot{token}/{method}"

    if params:
        data = json.dumps(params).encode("utf-8")
        req = request.Request(url, data=data, headers={"Content-Type": "application/json"})
    else:
        req = request.Request(url)

    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"HTTP {e.code}: {body[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _api_call_file(
    method: str,
    fields: dict,
    file_field: str,
    file_path: str,
    file_name: str | None = None,
) -> dict:
    """
    Make a Telegram Bot API call with a local file upload (multipart/form-data).

    Usa apenas stdlib (urllib + io) — sem dependências externas.

    Args:
        method: Telegram API method (ex: "sendAudio", "sendPhoto").
        fields: Campos de texto do formulário (ex: {"chat_id": "123"}).
        file_field: Nome do campo de arquivo no form (ex: "audio", "photo").
        file_path: Caminho do arquivo local a enviar.
        file_name: Nome do arquivo no upload. None = basename do path.
    """
    import mimetypes

    token = _get_token()
    url = f"https://api.telegram.org/bot{token}/{method}"

    mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    if not file_name:
        file_name = os.path.basename(file_path)

    boundary = f"RLMBoundary{os.getpid()}"
    body = BytesIO()
    crlf = b"\r\n"

    # Campos de texto
    for key, value in fields.items():
        body.write(f"--{boundary}".encode() + crlf)
        body.write(f'Content-Disposition: form-data; name="{key}"'.encode() + crlf + crlf)
        body.write(str(value).encode("utf-8") + crlf)

    # Campo de arquivo
    body.write(f"--{boundary}".encode() + crlf)
    body.write(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"'.encode()
        + crlf
    )
    body.write(f"Content-Type: {mime_type}".encode() + crlf + crlf)
    with open(file_path, "rb") as f:
        body.write(f.read())
    body.write(crlf + f"--{boundary}--".encode() + crlf)

    body_bytes = body.getvalue()
    req = request.Request(
        url,
        data=body_bytes,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"HTTP {e.code}: {body_err[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Public Functions (available in REPL)
# ---------------------------------------------------------------------------

def send_message(chat_id: int, text: str, parse_mode: str = "Markdown") -> str:
    """
    Send a text message to a Telegram chat.
    
    Args:
        chat_id: The Telegram chat ID to send to.
        text: The message text.
        parse_mode: "Markdown" or "HTML" (default: "Markdown").
        
    Returns:
        Status string like 'Message sent to 12345' or error description.
    """
    result = _api_call("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    })
    if result.get("ok"):
        return f"Message sent to {chat_id}"
    return f"Error: {result.get('error', result.get('description', 'Unknown'))}"


def get_updates(offset: int = 0, limit: int = 10, timeout: int = 0) -> list[dict]:
    """
    Get recent updates (messages) from the Telegram bot.
    
    Args:
        offset: Update ID offset (get updates after this ID).
        limit: Max number of updates to retrieve.
        timeout: Long polling timeout in seconds (0 = no wait).
        
    Returns:
        List of update dictionaries.
    """
    result = _api_call("getUpdates", {
        "offset": offset,
        "limit": limit,
        "timeout": timeout,
    })
    if result.get("ok"):
        return result.get("result", [])
    return [{"error": result.get("error", "Unknown")}]


def reply_to(chat_id: int, message_id: int, text: str) -> str:
    """
    Reply to a specific message in a Telegram chat.
    
    Args:
        chat_id: The Telegram chat ID.
        message_id: The message ID to reply to.
        text: The reply text.
        
    Returns:
        Status string.
    """
    result = _api_call("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "reply_to_message_id": message_id,
        "parse_mode": "Markdown",
    })
    if result.get("ok"):
        return f"Replied to message {message_id} in chat {chat_id}"
    return f"Error: {result.get('error', result.get('description', 'Unknown'))}"


def get_chat_info(chat_id: int) -> dict:
    """
    Get information about a Telegram chat.
    
    Args:
        chat_id: The Telegram chat ID.
        
    Returns:
        Dictionary with chat information.
    """
    result = _api_call("getChat", {"chat_id": chat_id})
    if result.get("ok"):
        return result.get("result", {})
    return {"error": result.get("error", "Unknown")}


def send_document(chat_id: int, document_url: str, caption: str = "") -> str:
    """
    Send a document (file) to a Telegram chat via URL.
    
    Args:
        chat_id: The Telegram chat ID.
        document_url: URL of the document to send.
        caption: Optional caption for the document.
        
    Returns:
        Status string.
    """
    params = {"chat_id": chat_id, "document": document_url}
    if caption:
        params["caption"] = caption
    result = _api_call("sendDocument", params)
    if result.get("ok"):
        return f"Document sent to {chat_id}"
    return f"Error: {result.get('error', result.get('description', 'Unknown'))}"


def send_audio(chat_id: int, audio_source: str, caption: str = "", title: str = "") -> str:
    """
    Envia arquivo de áudio (mp3/wav/etc.) para um chat Telegram.

    Usa sendAudio (exibe como música/player), suportando:
    - URL pública  — repassa diretamente para a API Telegram.
    - Arquivo local — faz upload via multipart/form-data.

    Ideal para respostas TTS (sintetizadas via openai.audio.speech.create).

    Args:
        chat_id: ID do chat Telegram.
        audio_source: URL HTTP/HTTPS ou caminho de arquivo local.
        caption: Legenda opcional.
        title: Título opcional (exibido no player).

    Returns:
        Status string.
    """
    if audio_source.startswith(("http://", "https://")):
        params: dict = {"chat_id": chat_id, "audio": audio_source}
        if caption:
            params["caption"] = caption
        if title:
            params["title"] = title
        result = _api_call("sendAudio", params)
    else:
        fields: dict = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
        if title:
            fields["title"] = title
        result = _api_call_file("sendAudio", fields, "audio", audio_source)

    if result.get("ok"):
        return f"Audio sent to {chat_id}"
    return f"Error: {result.get('error', result.get('description', 'Unknown'))}"


def send_photo(chat_id: int, photo_source: str, caption: str = "") -> str:
    """
    Envia foto para um chat Telegram.

    Aceita:
    - URL pública  — repassa para a API Telegram.
    - Arquivo local — faz upload via multipart/form-data.

    Args:
        chat_id: ID do chat Telegram.
        photo_source: URL HTTP/HTTPS ou caminho de arquivo local.
        caption: Legenda opcional.

    Returns:
        Status string.
    """
    if photo_source.startswith(("http://", "https://")):
        params: dict = {"chat_id": chat_id, "photo": photo_source}
        if caption:
            params["caption"] = caption
        result = _api_call("sendPhoto", params)
    else:
        fields: dict = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
        result = _api_call_file("sendPhoto", fields, "photo", photo_source)

    if result.get("ok"):
        return f"Photo sent to {chat_id}"
    return f"Error: {result.get('error', result.get('description', 'Unknown'))}"


def download_telegram_file(file_id: str) -> bytes:
    """
    Baixa um arquivo do Telegram dado seu file_id.

    Útil para processar mensagens de voz recebidas (voice messages):
      1. Obtenha o file_id de payload["voice"]["file_id"]
      2. Chame esta função para obter os bytes raw do arquivo .ogg
      3. Passe os bytes para transcribe_audio() do plugin audio

    Args:
        file_id: O file_id do arquivo Telegram.

    Returns:
        Bytes raw do arquivo.

    Raises:
        ValueError: Se getFile falhar.
    """
    result = _api_call("getFile", {"file_id": file_id})
    if not result.get("ok"):
        raise ValueError(
            f"getFile falhou para file_id='{file_id}': "
            f"{result.get('error', result.get('description', 'Unknown'))}"
        )
    file_path_remote = result["result"]["file_path"]
    token = _get_token()
    download_url = f"https://api.telegram.org/file/bot{token}/{file_path_remote}"
    try:
        with request.urlopen(download_url, timeout=60) as resp:
            return resp.read()
    except Exception as e:
        raise ValueError(f"Falha ao baixar arquivo Telegram: {e}") from e

# ---------------------------------------------------------------------------
# Channel Registry Hook
# ---------------------------------------------------------------------------
try:
    from rlm.plugins.channel_registry import ChannelAdapter, ChannelRegistry

    class TelegramAdapter(ChannelAdapter):
        """
        Adapter Telegram concreto para o ChannelRegistry.

        send_message(): envia texto via sendMessage.
        send_media():   detecta tipo de mídia pelo extension e despacha:
          - .mp3/.wav/.ogg/.aac/.flac/.m4a/.opus → send_audio()  (player Telegram)
          - .jpg/.jpeg/.png/.gif/.webp            → send_photo()  (imagem inline)
          - demais                                → send_document() (arquivo genérico)
        """

        # Extensions para roteamento automático em send_media()
        _AUDIO_EXTS: frozenset[str] = frozenset(
            {".mp3", ".mp4", ".wav", ".ogg", ".aac", ".flac", ".m4a", ".opus"}
        )
        _PHOTO_EXTS: frozenset[str] = frozenset(
            {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        )

        def send_message(self, target_id: str, text: str) -> bool:
            """Envia mensagem de texto. Retorna True se enviado com sucesso."""
            res = send_message(int(target_id), text)
            return not res.startswith("Error:")

        def send_media(self, target_id: str, media_url_or_path: str, caption: str = "") -> bool:
            """
            Envia mídia detectando o tipo pelo extension do caminho/URL.

            Roteamento:
              áudio → sendAudio  (player de música no Telegram)
              imagem → sendPhoto  (foto inline)
              demais → sendDocument (arquivo)
            """
            chat_id = int(target_id)
            # Extrai extensão sem query string
            raw_path = media_url_or_path.split("?")[0].split("#")[0]
            ext = os.path.splitext(raw_path)[-1].lower()

            if ext in self._AUDIO_EXTS:
                res = send_audio(chat_id, media_url_or_path, caption)
            elif ext in self._PHOTO_EXTS:
                res = send_photo(chat_id, media_url_or_path, caption)
            else:
                res = send_document(chat_id, media_url_or_path, caption)

            return not res.startswith("Error:")

    # Self-register
    ChannelRegistry.register("telegram", TelegramAdapter())
except ImportError:
    pass  # Allow standalone usage without the registry
