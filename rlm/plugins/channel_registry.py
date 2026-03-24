"""
Universal Channel Registry — Phase 9.2 / 11.3

Inspired by OpenClaw's src/channels/registry.ts.
Decouples the RLM's `reply()` / `reply_audio()` tools from plataformas específicas.
O REPL não precisa saber *onde* o usuário está — só chama reply() ou reply_audio().
"""

import abc
import os
import tempfile
import typing
from dataclasses import dataclass
from typing import Dict, Optional

from rlm.core.structured_log import get_logger

log = get_logger("channels")


def sanitize_text_payload(value: object) -> str:
    """Normalize outbound text so malformed surrogates do not crash UTF-8 encoders."""
    text = str(value or "")
    return text.encode("utf-8", errors="replace").decode("utf-8")


class ChannelAdapter(abc.ABC):
    """Abstract base class for all messaging channel plugins."""
    
    @abc.abstractmethod
    def send_message(self, target_id: str, text: str) -> bool:
        """Send a standard text message to the target."""
        pass
        
    @abc.abstractmethod
    def send_media(self, target_id: str, media_url_or_path: str, caption: str = "") -> bool:
        """Send media (audio, image, document) to the target."""
        pass


class ChannelRegistry:
    """
    Central router for outbound replies (text + TTS audio).
    Prefix mapping: "telegram" -> TelegramAdapter, "audio" -> AudioAdapter, etc.
    """
    
    _adapters: Dict[str, ChannelAdapter] = {}

    @classmethod
    def register(cls, prefix: str, adapter: ChannelAdapter):
        """Registers a channel adapter to a prefix (e.g., 'telegram')."""
        cls._adapters[prefix] = adapter
        log.info(f"Registered channel adapter: {prefix}")

    @classmethod
    def get_adapter(cls, prefix: str) -> Optional[ChannelAdapter]:
        return cls._adapters.get(prefix)
        
    @classmethod
    def reply(cls, client_id: str, message: str) -> bool:
        """
        Envia resposta de texto ao usuário pelo canal original.

        Parseia client_id (ex: 'telegram:12345') e despacha para o adapter correto.
        """
        if ":" not in client_id:
            log.warn(f"Cannot route reply: Invalid client_id format '{client_id}'. Expected 'prefix:id'.")
            return False
            
        prefix, target_id = client_id.split(":", 1)
        message = sanitize_text_payload(message)
        adapter = cls.get_adapter(prefix)
        
        if not adapter:
            log.error(f"Cannot reply: No adapter registered for channel prefix '{prefix}'.")
            return False
            
        try:
            return adapter.send_message(target_id, message)
        except Exception as e:
            log.error(f"Error sending message via {prefix}: {e}")
            return False

    @classmethod
    def reply_audio(
        cls,
        client_id: str,
        text: str,
        voice: str = "alloy",
        output_format: str = "mp3",
    ) -> bool:
        """
        Sintetiza fala (TTS) e envia como arquivo de áudio pelo canal original.

        Fluxo:
          1. Chama synthesize_speech(text, voice) → bytes mp3
          2. Salva em arquivo temporário
          3. Chama adapter.send_media(target_id, temp_path)
          4. Remove o arquivo temporário

        Requer: plugin 'audio' instalado (openai >= 1.0.0 + OPENAI_API_KEY).

        Args:
            client_id: Ex: "telegram:12345" ou "audio:session_001".
            text: Texto para sintetizar.
            voice: Voz TTS. Opções: alloy, echo, fable, onyx, nova, shimmer.
            output_format: Formato do áudio. Default "mp3".

        Returns:
            True se enviado com sucesso, False caso contrário.
        """
        if ":" not in client_id:
            log.warn(f"reply_audio: client_id inválido '{client_id}'.")
            return False

        prefix, target_id = client_id.split(":", 1)
        text = sanitize_text_payload(text)
        adapter = cls.get_adapter(prefix)

        if not adapter:
            log.error(f"reply_audio: Nenhum adapter para prefixo '{prefix}'.")
            return False

        tmp_path: str | None = None
        try:
            from rlm.plugins.audio import synthesize_speech  # lazy import

            # Sintetiza e salva em temp
            fd, tmp_path = tempfile.mkstemp(
                suffix=f".{output_format}",
                prefix=f"rlm_tts_{target_id}_",
            )
            os.close(fd)
            synthesize_speech(text, voice=voice, output_format=output_format, output_path=tmp_path)

            # Entrega via adapter
            return adapter.send_media(target_id, tmp_path, caption="")

        except ImportError:
            log.error(
                "reply_audio requer o pacote 'openai'. "
                "Instale com: pip install 'openai>=1.0.0'"
            )
            return False
        except Exception as e:
            log.error(f"reply_audio falhou para '{client_id}': {e}")
            return False
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    @classmethod
    def send_media(cls, client_id: str, media_url_or_path: str, caption: str = "") -> bool:
        """
        Envia mídia diretamente (sem TTS) pelo canal original.

        Útil para enviar imagens geradas, capturas de tela, arquivos de dados.

        Args:
            client_id: Ex: "telegram:12345".
            media_url_or_path: URL ou caminho local do arquivo de mídia.
            caption: Legenda opcional.
        """
        if ":" not in client_id:
            log.warn(f"send_media: client_id inválido '{client_id}'.")
            return False

        prefix, target_id = client_id.split(":", 1)
        caption = sanitize_text_payload(caption)
        adapter = cls.get_adapter(prefix)

        if not adapter:
            log.error(f"send_media: Nenhum adapter para prefixo '{prefix}'.")
            return False

        try:
            return adapter.send_media(target_id, media_url_or_path, caption)
        except Exception as e:
            log.error(f"send_media falhou para '{client_id}': {e}")
            return False
