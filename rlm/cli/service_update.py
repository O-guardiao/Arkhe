from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from rlm.cli.context import CliContext


def _looks_like_checkout(path: Path) -> bool:
    return (path / ".git").exists()


def _walk_to_checkout_root(path: Path) -> Path | None:
    current = path.resolve()
    for candidate in (current, *current.parents):
        if _looks_like_checkout(candidate):
            return candidate
    return None


def _package_checkout_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_project_root(context: CliContext, target_path: str | None) -> Path | None:
    candidates: list[Path] = []
    seen: set[Path] = set()

    def add_candidate(path: Path | None) -> None:
        if path is None:
            return
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        candidates.append(resolved)

    if target_path:
        add_candidate(Path(target_path).expanduser())

    add_candidate(context.paths.project_root)
    add_candidate(_package_checkout_root())

    configured_repo_dir = context.env.get("ARKHE_REPO_DIR", "").strip()
    if configured_repo_dir:
        add_candidate(Path(configured_repo_dir).expanduser())

    configured_install_dir = context.env.get("ARKHE_INSTALL_DIR", "").strip()
    if configured_install_dir:
        add_candidate(Path(configured_install_dir).expanduser() / "repo")

    add_candidate(context.home / ".arkhe" / "repo")

    for candidate in candidates:
        checkout_root = _walk_to_checkout_root(candidate)
        if checkout_root is not None:
            return checkout_root
    return None


def update_installation_impl(
    context: CliContext,
    *,
    check_only: bool,
    restart: bool,
    target_path: str | None,
    info: Callable[[str], None],
    ok: Callable[[str], None],
    err: Callable[[str], None],
    services_are_running: Callable[[], bool],
    stop_services: Callable[[], int],
    start_services: Callable[[], int],
) -> int:
    project_root = _resolve_project_root(context, target_path)

    if project_root is None:
        if target_path:
            err(f"Nenhum checkout git do Arkhe foi encontrado em '{target_path}'.")
        else:
            err("Nenhum checkout git do Arkhe foi encontrado. Rode o comando dentro do repo, use --path ou instale em ~/.arkhe/repo.")
        return 1

    if not context.has_tool("git"):
        err("`git` não encontrado no PATH.")
        return 1

    if not context.has_tool("uv"):
        err("`uv` não encontrado no PATH.")
        return 1

    info(f"Usando checkout em {project_root}")
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

    has_local_changes = bool(status.stdout.strip())
    if has_local_changes:
        info("Mudanças locais detectadas — guardando com git stash...")
        stash = subprocess.run(
            ["git", "stash", "--include-untracked", "-m", "arkhe-update-autostash"],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        if stash.returncode != 0:
            err((stash.stderr or stash.stdout or "Falha ao fazer git stash.").strip())
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

    def _restore_stash() -> None:
        if not has_local_changes:
            return
        info("Restaurando mudanças locais com git stash pop...")
        pop = subprocess.run(
            ["git", "stash", "pop"],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        if pop.returncode != 0:
            err("Conflito ao restaurar mudanças locais. Resolva com: git stash show -p | git apply --3way")
            info("Suas mudanças estão salvas no stash. Use 'git stash list' para ver.")

    if check_only:
        if behind_count == 0 and ahead_count == 0:
            ok("Checkout já está sincronizado com origin.")
        elif behind_count == 0:
            ok("Checkout local está à frente do remoto; nada para baixar.")
        else:
            ok(f"Há {behind_count} commit(s) pendente(s) em origin/{branch}.")
        _restore_stash()
        return 0

    if behind_count == 0:
        ok("Nenhuma atualização remota disponível.")
        _restore_stash()
        return 0

    if ahead_count > 0:
        _restore_stash()
        err(f"Checkout local divergiu de origin/{branch} ({ahead_count} commit(s) à frente, {behind_count} atrás). Faça rebase/merge manual antes do update.")
        return 1

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

    _restore_stash()

    if restart and services_are_running():
        info("Reiniciando serviços do RLM...")
        stop_rc = stop_services()
        if stop_rc != 0:
            err("Falha ao parar serviços antes do restart.")
            return stop_rc
        start_rc = start_services()
        if start_rc != 0:
            err("Falha ao iniciar serviços após o update.")
            return start_rc
        ok("Serviços reiniciados.")
    elif restart:
        info("Serviços não estavam ativos; nenhum restart necessário.")

    return 0