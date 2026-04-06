from __future__ import annotations

import argparse
import os
import re
import urllib.error as urllib_error
import urllib.request as urllib_request
from pathlib import Path

from rlm.cli.context import CliContext, print_error, print_success


def _resolve_skills_dir(context: CliContext) -> Path:
    return context.paths.skills_dir


def cmd_skill_list(args: argparse.Namespace, *, context: CliContext | None = None) -> int:  # noqa: ARG001
    """Lista todas as skills instaladas e seu status."""
    current_context = context if context is not None else CliContext.from_environment()
    skills_dir = _resolve_skills_dir(current_context)

    if not skills_dir.exists():
        print_error(f"Diretório de skills não encontrado: {skills_dir}")
        return 1

    skill_dirs = sorted(directory for directory in skills_dir.iterdir() if directory.is_dir())
    if not skill_dirs:
        print("Nenhuma skill instalada.")
        return 0

    console = None
    table = None
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
        table.add_column("Skill", style="bold")
        table.add_column("Versão")
        table.add_column("Descrição")
        table.add_column("Status")
        rich_enabled = True
    except ImportError:
        rich_enabled = False
        print(f"{'Skill':<20} {'Versão':<8} Descrição")
        print("-" * 70)

    for skill_path in skill_dirs:
        skill_md = skill_path / "SKILL.md"
        name = skill_path.name
        version = "-"
        description = ""
        has_skill_md = skill_md.exists()

        if has_skill_md:
            content = skill_md.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"^\+\+\+(.*?)\+\+\+", content, re.DOTALL | re.MULTILINE)
            if match:
                frontmatter = match.group(1)
                version_match = re.search(r'version\s*=\s*["\']?([^"\'\\n]+)["\']?', frontmatter)
                description_match = re.search(r'description\s*=\s*["\']([^"\']+)["\']', frontmatter)
                if version_match:
                    version = version_match.group(1).strip()
                if description_match:
                    description = description_match.group(1).strip()[:60]

        status = "✓" if has_skill_md else "⚠ sem SKILL.md"
        if rich_enabled and table is not None:
            table.add_row(name, version, description or "-", status)
        else:
            print(f"{name:<20} {version:<8} {description or '-'}")

    if rich_enabled and console is not None and table is not None:
        console.print(table)
        console.print(f"\n[dim]{len(skill_dirs)} skill(s) em {skills_dir}[/]")
    else:
        print(f"\n{len(skill_dirs)} skill(s)")

    return 0


def cmd_skill_install(args: argparse.Namespace, *, context: CliContext | None = None) -> int:
    """
    Instala uma skill remotamente.

    Formatos aceitos:
        arkhe skill install github:usuario/repositorio
        arkhe skill install github:usuario/repositorio@branch
        arkhe skill install https://raw.githubusercontent.com/.../SKILL.md
    """
    current_context = context if context is not None else CliContext.from_environment()
    source = args.source.strip()
    skills_dir = _resolve_skills_dir(current_context)

    raw_url = ""
    skill_name = ""

    match = re.match(r"^github:([^/]+)/([^@]+)(?:@(.+))?$", source)
    if match:
        gh_user, gh_repo, branch = match.group(1), match.group(2), match.group(3) or "main"
        raw_url = f"https://raw.githubusercontent.com/{gh_user}/{gh_repo}/{branch}/SKILL.md"
        skill_name = gh_repo.lower().replace("-", "_").replace(".", "_")
    elif source.startswith("https://") or source.startswith("http://"):
        raw_url = source
        skill_name = source.rstrip("/").split("/")[-2] if "/SKILL.md" in source else source.rstrip("/").split("/")[-1]
        skill_name = re.sub(r"[^a-z0-9_]", "_", skill_name.lower())
    else:
        print_error(
            f"Formato inválido: '{source}'\n"
            "  Use: github:usuario/repo  ou  github:usuario/repo@branch"
        )
        return 1

    print(f"Baixando skill de {raw_url} ...")
    try:
        request = urllib_request.Request(raw_url, headers={"User-Agent": "Arkhe-CLI/1.0"})
        with urllib_request.urlopen(request, timeout=15) as response:
            skill_content = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        if exc.code == 404:
            print_error(f"SKILL.md não encontrado em {raw_url} (404)")
        else:
            print_error(f"Erro HTTP {exc.code} ao baixar {raw_url}")
        return 1
    except Exception as exc:
        print_error(f"Falha ao baixar skill: {exc}")
        return 1

    if "+++" not in skill_content and "---" not in skill_content:
        print_error(
            "O arquivo baixado não parece ser um SKILL.md válido "
            "(sem frontmatter +++ ou ---)."
        )
        return 1

    name_match = re.search(r'name\s*=\s*["\']?([A-Za-z0-9_\-]+)["\']?', skill_content)
    if name_match:
        skill_name = re.sub(r"[^a-z0-9_]", "_", name_match.group(1).lower())

    dest_dir = skills_dir / skill_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / "SKILL.md"

    if dest_file.exists() and not getattr(args, "force", False):
        print_error(
            f"Skill '{skill_name}' já existe em {dest_file}. "
            "Use --force para sobrescrever."
        )
        return 1

    dest_file.write_text(skill_content, encoding="utf-8")
    print_success(f"Skill '{skill_name}' instalada em {dest_file}")
    print("  Reinicie o servidor para ativar: arkhe stop && arkhe start")
    return 0