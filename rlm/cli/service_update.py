from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from rlm.cli.context import CliContext


def update_installation_impl(
    context: CliContext,
    *,
    check_only: bool,
    restart: bool,
    info: Callable[[str], None],
    ok: Callable[[str], None],
    err: Callable[[str], None],
    services_are_running: Callable[[], bool],
    stop_services: Callable[[], int],
    start_services: Callable[[], int],
) -> int:
    project_root = context.paths.project_root

    if not (project_root / ".git").exists():
        err("`rlm update` requer um checkout git do projeto na pasta atual.")
        return 1

    if not context.has_tool("git"):
        err("`git` não encontrado no PATH.")
        return 1

    if not context.has_tool("uv"):
        err("`uv` não encontrado no PATH.")
        return 1

    info("Validando worktree local...")
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        err((status.stderr or status.stdout or "Falha ao ler estado do git.").strip())
        return 1
    if status.stdout.strip():
        err("Há mudanças locais não commitadas. Faça commit/stash antes de atualizar.")
        return 1

    branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if branch_result.returncode != 0:
        err((branch_result.stderr or branch_result.stdout or "Falha ao detectar branch atual.").strip())
        return 1
    branch = branch_result.stdout.strip()
    if not branch or branch == "HEAD":
        err("Branch atual inválida para update automático.")
        return 1

    info(f"Buscando updates remotos para '{branch}'...")
    fetch = subprocess.run(
        ["git", "fetch", "origin", branch, "--quiet"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        err((fetch.stderr or fetch.stdout or "Falha no git fetch.").strip())
        return 1

    rev_list = subprocess.run(
        ["git", "rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if rev_list.returncode != 0:
        err((rev_list.stderr or rev_list.stdout or "Falha ao comparar commits.").strip())
        return 1

    counts = rev_list.stdout.strip().split()
    if len(counts) != 2:
        err("Saída inesperada do git rev-list ao comparar atualizações.")
        return 1

    ahead_count = int(counts[0])
    behind_count = int(counts[1])

    if check_only:
        if behind_count == 0 and ahead_count == 0:
            ok("Checkout já está sincronizado com origin.")
        elif behind_count == 0:
            ok("Checkout local está à frente do remoto; nada para baixar.")
        else:
            ok(f"Há {behind_count} commit(s) pendente(s) em origin/{branch}.")
        return 0

    if behind_count == 0:
        ok("Nenhuma atualização remota disponível.")
        return 0

    info("Aplicando git pull --ff-only...")
    pull = subprocess.run(
        ["git", "pull", "--ff-only", "origin", branch],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if pull.returncode != 0:
        err((pull.stderr or pull.stdout or "Falha no git pull.").strip())
        return 1
    ok(f"Código atualizado: {behind_count} commit(s) aplicados.")

    info("Reinstalando dependências com uv sync...")
    sync = subprocess.run(
        ["uv", "sync"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if sync.returncode != 0:
        err((sync.stderr or sync.stdout or "Falha no uv sync.").strip())
        return 1
    ok("Dependências sincronizadas.")

    if restart and services_are_running():
        info("Reiniciando serviços do RLM...")
        stop_services()
        start_services()
        ok("Serviços reiniciados.")
    elif restart:
        info("Serviços não estavam ativos; nenhum restart necessário.")

    return 0