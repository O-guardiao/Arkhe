"""
parser.py — Parsing puro de Markdown/Obsidian → Note.

Tudo aqui é função top-level e determinística (sem estado global, sem I/O),
para que possa rodar dentro de um ProcessPoolExecutor (precisa ser picklable).

Extrai: frontmatter YAML simples, título, tags (frontmatter + inline #tag),
wikilinks [[alvo|alias#heading]], headings, e divide o corpo em chunks
ancorados no heading mais próximo respeitando um orçamento de caracteres.
"""

from __future__ import annotations

import json
import re

from arkhe_obsidian_rag.models import Chunk, Note, _approx_tokens

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_WIKILINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
_INLINE_TAG_RE = re.compile(r"(?:^|[\s(])#([A-Za-z0-9][\w/\-]*)")
_INLINE_CODE_RE = re.compile(r"`[^`]*`")


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Separa frontmatter YAML (subset) do corpo. Retorna (fm, body)."""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}, content
    fm: dict = {}
    for line in m.group(1).split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value.startswith("[") and value.endswith("]"):
            try:
                fm[key] = json.loads(value)
            except json.JSONDecodeError:
                fm[key] = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
        elif value.replace(".", "", 1).isdigit():
            fm[key] = float(value) if "." in value else int(value)
        else:
            fm[key] = value.strip("\"'")
    return fm, m.group(2)


def _strip_code(text: str) -> str:
    """Remove blocos cercados e código inline (para extração de tags/links)."""
    out_lines: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        out_lines.append(line)
    return _INLINE_CODE_RE.sub(" ", "\n".join(out_lines))


def _normalize_link(target: str) -> str:
    """[[Pasta/Nota#Heading|Alias]] → 'Pasta/Nota' (sem alias/heading/bloco)."""
    target = target.split("|", 1)[0]
    target = target.split("#", 1)[0]
    target = target.split("^", 1)[0]
    return target.strip()


def extract_tags(frontmatter: dict, clean_body: str) -> list[str]:
    """Tags do frontmatter + tags inline (#tag), deduplicadas, ordenadas."""
    tags: set[str] = set()
    fm_tags = frontmatter.get("tags")
    if isinstance(fm_tags, list):
        tags.update(str(t).lstrip("#").strip() for t in fm_tags if str(t).strip())
    elif isinstance(fm_tags, str) and fm_tags.strip():
        tags.update(t.lstrip("#").strip() for t in re.split(r"[,\s]+", fm_tags) if t.strip())
    for m in _INLINE_TAG_RE.finditer(clean_body):
        tags.add(m.group(1))
    return sorted(t for t in tags if t)


def extract_wikilinks(clean_body: str) -> list[str]:
    """Alvos de wikilinks, normalizados e deduplicados (preservando ordem)."""
    seen: dict[str, None] = {}
    for m in _WIKILINK_RE.finditer(clean_body):
        tgt = _normalize_link(m.group(1))
        if tgt:
            seen.setdefault(tgt, None)
    return list(seen.keys())


def _iter_sections(body: str):
    """
    Gera (heading, start_line, texto_da_secao) varrendo o corpo, respeitando
    blocos de código (headings dentro de ``` não contam).
    """
    lines = body.split("\n")
    current_heading = ""
    buf: list[str] = []
    section_start = 1
    in_fence = False
    for i, line in enumerate(lines, start=1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            buf.append(line)
            continue
        hm = _HEADING_RE.match(line) if not in_fence else None
        if hm:
            if buf and any(s.strip() for s in buf):
                yield current_heading, section_start, "\n".join(buf).strip()
            current_heading = hm.group(2).strip()
            buf = [line]
            section_start = i
        else:
            buf.append(line)
    if buf and any(s.strip() for s in buf):
        yield current_heading, section_start, "\n".join(buf).strip()


def _split_long(text: str, max_chars: int) -> list[str]:
    """Quebra um texto grande em pedaços <= max_chars, preferindo parágrafos."""
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    buf = ""
    for para in text.split("\n\n"):
        if not para.strip():
            continue
        if len(buf) + len(para) + 2 > max_chars and buf:
            parts.append(buf.strip())
            buf = ""
        if len(para) > max_chars:
            # parágrafo único enorme: fatia bruta
            if buf:
                parts.append(buf.strip())
                buf = ""
            for j in range(0, len(para), max_chars):
                parts.append(para[j : j + max_chars])
        else:
            buf += ("\n\n" + para) if buf else para
    if buf.strip():
        parts.append(buf.strip())
    return parts


def chunk_body(note_path: str, body: str, max_chunk_chars: int) -> list[Chunk]:
    """Divide o corpo em chunks ancorados em headings, respeitando o budget."""
    chunks: list[Chunk] = []
    idx = 0
    for heading, start_line, section in _iter_sections(body):
        for piece in _split_long(section, max_chunk_chars):
            piece = piece.strip()
            if not piece:
                continue
            chunks.append(
                Chunk(
                    note_path=note_path,
                    chunk_id=f"{note_path}#{idx}",
                    index=idx,
                    heading=heading,
                    text=piece,
                    start_line=start_line,
                    tokens=_approx_tokens(piece),
                )
            )
            idx += 1
    return chunks


def _derive_title(frontmatter: dict, rel_path: str, body: str) -> str:
    fm_title = frontmatter.get("title")
    if isinstance(fm_title, str) and fm_title.strip():
        return fm_title.strip()
    for line in body.split("\n"):
        hm = _HEADING_RE.match(line)
        if hm and hm.group(1) == "#":
            return hm.group(2).strip()
    name = rel_path.rsplit("/", 1)[-1]
    return name[:-3] if name.endswith(".md") else name


def parse_note(
    rel_path: str,
    content: str,
    mtime: float,
    size: int,
    max_chunk_chars: int = 1200,
) -> Note:
    """Parser completo: string de markdown → Note. Função pura/picklable."""
    frontmatter, body = parse_frontmatter(content)
    clean = _strip_code(body)
    tags = extract_tags(frontmatter, clean)
    wikilinks = extract_wikilinks(clean)
    headings = [hm.group(2).strip() for line in body.split("\n") if (hm := _HEADING_RE.match(line))]
    title = _derive_title(frontmatter, rel_path, body)
    chunks = chunk_body(rel_path, body, max_chunk_chars)
    return Note(
        path=rel_path,
        title=title,
        frontmatter=frontmatter,
        tags=tags,
        wikilinks=wikilinks,
        headings=headings,
        body=body,
        chunks=chunks,
        mtime=mtime,
        size=size,
        word_count=len(clean.split()),
    )


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Tokenização lexical para BM25 (unicode-aware, lowercase)."""
    return _TOKEN_RE.findall(text.lower())
