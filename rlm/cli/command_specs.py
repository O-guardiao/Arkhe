from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rlm.cli.commands.channel import cmd_channel_list, cmd_channel_status, cmd_channel_probe
from rlm.cli.commands.client import cmd_client_add, cmd_client_list, cmd_client_revoke, cmd_client_status
from rlm.cli.commands.doctor import cmd_doctor
from rlm.cli.commands.peer import cmd_peer_add
from rlm.cli.commands.service import cmd_start, cmd_status, cmd_stop, cmd_update
from rlm.cli.commands.setup import cmd_setup
from rlm.cli.commands.skill import cmd_skill_install, cmd_skill_list
from rlm.cli.commands.token import cmd_token_rotate
from rlm.cli.commands.tui import cmd_tui
from rlm.cli.commands.version import cmd_version


@dataclass(frozen=True, slots=True)
class ArgumentSpec:
    flags: tuple[str, ...]
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    help: str
    handler: Any | None = None
    aliases: tuple[str, ...] = ()
    description: str | None = None
    epilog: str | None = None
    arguments: tuple[ArgumentSpec, ...] = ()
    metavar: str | None = None
    subcommands: tuple[CommandSpec, ...] = ()


CLI_DESCRIPTION = "Arkhe — CLI de gerenciamento"
CLI_EPILOG = """
Exemplos:
    arkhe setup              # Wizard de primeira instalação
    arkhe start              # Inicia API + WebSocket em background
    arkhe start --foreground # Inicia no terminal (logs ao vivo)
    arkhe status             # Mostra processos ativos e config
    arkhe doctor             # Diagnóstico operacional completo
    arkhe token rotate       # Rotaciona tokens de segurança
    arkhe peer add --name laptop --pubkey <KEY> --ip 10.0.0.2

Aliases úteis:
    arkhe diag               # Alias de doctor
    arkhe ps                 # Alias de status
    arkhe channel ls         # Alias de channel list
    arkhe skill ls           # Alias de skill list

Compatibilidade:
    rlm ...                  # Alias legado ainda suportado
"""


def get_command_specs() -> tuple[CommandSpec, ...]:
    return (
        CommandSpec(
            name="tui",
            help="Abre o workbench TUI da sessão viva",
            handler=cmd_tui,
            description="Inicia um painel operacional em terminal sobre a mesma sessão recursiva viva, com árvores de branches, eventos, timeline e controles do operador.",
            epilog="""
Exemplos:
    arkhe tui
    arkhe tui --client-id tui:demo
    arkhe tui --once

Controles:
    Texto livre            envia prompt ao runtime
    /pause /resume         controla execução
    /focus /winner         direciona branches
    /priority /checkpoint  ajusta estratégia e persistência
""",
            arguments=(
                ArgumentSpec(flags=("--client-id",), kwargs={"default": None, "help": "Client id da sessão viva (default: tui:default)"}),
                ArgumentSpec(flags=("--refresh-interval",), kwargs={"type": float, "default": 0.75, "help": "Intervalo de atualização do painel em segundos"}),
                ArgumentSpec(flags=("--once",), kwargs={"action": "store_true", "help": "Renderiza o painel uma vez e encerra"}),
            ),
        ),
        CommandSpec(
            name="setup",
            help="Wizard interativo de instalação",
            handler=cmd_setup,
            description="Configura um ambiente Arkhe local ou remoto com wizard interativo.",
            arguments=(
                ArgumentSpec(
                    flags=("--flow",),
                    kwargs={
                        "choices": ["quickstart", "advanced"],
                        "default": None,
                        "help": "Modo do wizard: quickstart (defaults automáticos) ou advanced (configuração manual)",
                    },
                ),
            ),
        ),
        CommandSpec(
            name="start",
            help="Inicia o servidor Arkhe",
            handler=cmd_start,
            description="Inicia o runtime Arkhe, cujo destino principal é manter API, observabilidade e gateways de canais operando de forma contínua.",
            epilog="""
Exemplos:
    arkhe start
    arkhe start --foreground
    arkhe start --api-only

Recuperação:
    Se a subida falhar, verifique ~/.rlm/logs/api.log e rode arkhe doctor.
    Se houver conflito de porta, rode arkhe stop e valide o estado com arkhe status.
""",
            arguments=(
                ArgumentSpec(flags=("--foreground", "-f"), kwargs={"action": "store_true", "help": "Não lança em background (bloqueia o terminal)"}),
                ArgumentSpec(flags=("--api-only",), kwargs={"action": "store_true", "help": "Inicia somente o servidor FastAPI (sem WebSocket)"}),
                ArgumentSpec(flags=("--ws-only",), kwargs={"action": "store_true", "help": "Inicia somente o servidor WebSocket"}),
            ),
        ),
        CommandSpec(name="stop", help="Para o daemon Arkhe", handler=cmd_stop),
        CommandSpec(
            name="status",
            help="Mostra status dos processos e configuração",
            handler=cmd_status,
            aliases=("ps",),
            arguments=(
                ArgumentSpec(
                    flags=("--json",),
                    kwargs={
                        "action": "store_true",
                        "help": "Emite um snapshot estruturado do status operacional e do launcher-state",
                    },
                ),
            ),
        ),
        CommandSpec(name="version", help="Exibe versão do Arkhe", handler=cmd_version),
        CommandSpec(
            name="update",
            help="Atualiza checkout git e dependências",
            handler=cmd_update,
            description="Atualiza um checkout operacional do Arkhe e ressincroniza dependências do runtime.",
            epilog="""
Exemplos:
    arkhe update --check
    arkhe update
    arkhe update --no-restart
    arkhe update --path ~/.arkhe/repo

Recuperação:
    Se o update bloquear por worktree suja, faça commit ou stash primeiro.
    Se uv sync falhar, corrija o ambiente e repita antes de reiniciar os serviços.
""",
            arguments=(
                ArgumentSpec(flags=("--check",), kwargs={"action": "store_true", "help": "Apenas verifica se há commits remotos pendentes"}),
                ArgumentSpec(flags=("--no-restart",), kwargs={"action": "store_true", "help": "Não reinicia os serviços após atualizar"}),
                ArgumentSpec(flags=("--path",), kwargs={"default": None, "help": "Checkout do Arkhe a atualizar; default tenta detectar a instalação ativa"}),
            ),
        ),
        CommandSpec(
            name="doctor",
            help="Valida configuração e testa conexões",
            handler=cmd_doctor,
            aliases=("diag",),
            description="Executa diagnóstico operacional do Arkhe: runtime, tokens, servidor local e handshakes com gateways externos.",
            epilog="""
Exemplos:
    arkhe doctor
    arkhe doctor --launcher-state-json

Recuperação:
    Se o servidor estiver offline, suba com arkhe start antes de validar canais.
    Se tokens ou .env estiverem ausentes, rode arkhe setup ou arkhe token rotate.
""",
            arguments=(
                ArgumentSpec(
                    flags=("--launcher-state-json",),
                    kwargs={
                        "action": "store_true",
                        "help": "Emite apenas o diagnóstico estruturado do launcher-state para automação",
                    },
                ),
            ),
        ),
        CommandSpec(
            name="token",
            help="Gerencia tokens de segurança",
            metavar="<ação>",
            subcommands=(
                CommandSpec(name="rotate", help="Regenera todos os tokens de segurança do runtime", handler=cmd_token_rotate),
            ),
        ),
        CommandSpec(
            name="peer",
            help="Gerencia peers WireGuard",
            metavar="<ação>",
            subcommands=(
                CommandSpec(
                    name="add",
                    help="Adiciona peer WireGuard",
                    handler=cmd_peer_add,
                    arguments=(
                        ArgumentSpec(flags=("--name",), kwargs={"required": True, "help": "Nome do peer (ex: laptop)"}),
                        ArgumentSpec(flags=("--pubkey",), kwargs={"required": True, "help": "Chave pública WireGuard do peer"}),
                        ArgumentSpec(flags=("--ip",), kwargs={"required": True, "help": "IP do peer na VPN (ex: 10.0.0.2)"}),
                    ),
                ),
            ),
        ),
        CommandSpec(
            name="skill",
            help="Gerencia skills do Arkhe",
            metavar="<ação>",
            subcommands=(
                CommandSpec(name="list", help="Lista skills instaladas", handler=cmd_skill_list, aliases=("ls",)),
                CommandSpec(
                    name="install",
                    help="Instala skill remota",
                    handler=cmd_skill_install,
                    arguments=(
                        ArgumentSpec(flags=("source",), kwargs={"help": "Fonte da skill: github:usuario/repo  ou  github:usuario/repo@branch"}),
                        ArgumentSpec(flags=("--force",), kwargs={"action": "store_true", "help": "Sobrescreve se já existir"}),
                    ),
                ),
            ),
        ),
        CommandSpec(
            name="channel",
            help="Gerencia canais de mensagem",
            metavar="<ação>",
            subcommands=(
                CommandSpec(name="list", help="Lista canais disponíveis e seu estado", handler=cmd_channel_list, aliases=("ls",)),
                CommandSpec(name="status", help="Mostra status runtime dos canais via API", handler=cmd_channel_status),
                CommandSpec(
                    name="probe",
                    help="Executa probe sob demanda para um canal",
                    handler=cmd_channel_probe,
                    arguments=(
                        ArgumentSpec(flags=("channel_id",), kwargs={"help": "ID do canal (telegram, discord, whatsapp, slack, webchat)"}),
                    ),
                ),
            ),
        ),
        CommandSpec(
            name="client",
            help="Gerencia dispositivos/clientes registrados",
            metavar="<ação>",
            subcommands=(
                CommandSpec(
                    name="add",
                    help="Registra novo dispositivo e emite token",
                    handler=cmd_client_add,
                    arguments=(
                        ArgumentSpec(flags=("client_id",), kwargs={"help": "ID do cliente (ex: esp32-sala, iphone-demet)"}),
                        ArgumentSpec(flags=("--profile", "-p"), kwargs={"default": "default", "help": "Perfil de comportamento (referência [[profiles]] no rlm.toml)"}),
                        ArgumentSpec(flags=("--description", "-d"), kwargs={"default": "", "help": "Descrição do dispositivo"}),
                        ArgumentSpec(flags=("--context", "-c"), kwargs={"default": "", "help": "Context hint para o agente"}),
                        ArgumentSpec(flags=("--metadata", "-m"), kwargs={"default": None, "help": "JSON livre (ex: '{\"preferred_channel\": \"telegram:123\"}')"}),
                    ),
                ),
                CommandSpec(
                    name="list",
                    help="Lista clientes registrados",
                    handler=cmd_client_list,
                    aliases=("ls",),
                    arguments=(
                        ArgumentSpec(flags=("--all", "-a"), kwargs={"action": "store_true", "help": "Mostra também clientes revogados"}),
                    ),
                ),
                CommandSpec(
                    name="revoke",
                    help="Revoga acesso de um cliente (sem deletar)",
                    handler=cmd_client_revoke,
                    arguments=(
                        ArgumentSpec(flags=("client_id",), kwargs={"help": "ID do cliente a revogar"}),
                    ),
                ),
                CommandSpec(
                    name="status",
                    help="Mostra status detalhado de um cliente",
                    handler=cmd_client_status,
                    arguments=(
                        ArgumentSpec(flags=("client_id",), kwargs={"help": "ID do cliente"}),
                    ),
                ),
            ),
        ),
    )