/**
 * BranchTree — painel central inferior do TUI.
 *
 * Visualiza a árvore de ramificação de raciocínio do agente.
 * Cada "branch" representa uma linha de pensamento iniciada pelo Brain.
 *
 * Exemplo de render:
 *
 *  ▸ ROOT (3 filhos)
 *    ├─ branch-a [tool_call] openai.chat ✓ 312ms
 *    │  └─ branch-a.1 [memory_read] ctx_window ✓ 5ms
 *    └─ branch-b [web_search] query="price" ⟳ running...
 */

import chalk from "chalk";
import type { Rect } from "./workbench.js";
import { moveTo, padEnd, truncate } from "./ansi.js";

export type BranchStatus = "running" | "ok" | "error" | "cancelled";

export interface BranchNode {
  id: string;
  parentId?: string;
  label: string;
  status: BranchStatus;
  durationMs?: number | undefined;
  children: BranchNode[];
}

const STATUS_ICON: Record<BranchStatus, string> = {
  running:   chalk.yellow("⟳"),
  ok:        chalk.green("✓"),
  error:     chalk.red("✗"),
  cancelled: chalk.gray("⊘"),
};

export class BranchTree {
  private roots: BranchNode[] = [];
  private nodeIndex = new Map<string, BranchNode>();
  private readonly MAX_ROOTS = 20;

  constructor(private rect: Rect) {}

  updateRect(rect: Rect): void {
    this.rect = rect;
  }

  /** Adiciona ou atualiza um nó. Se `parentId` não existir, é tratado como raiz. */
  upsert(node: Omit<BranchNode, "children">): void {
    const existing = this.nodeIndex.get(node.id);
    if (existing) {
      if (node.parentId != null) {
        existing.parentId = node.parentId;
      } else {
        delete existing.parentId;
      }
      existing.label = node.label;
      existing.status = node.status;
      existing.durationMs = node.durationMs;
      this._rebuild();
      return;
    }
    const newNode: BranchNode = { ...node, children: [] };
    this.nodeIndex.set(node.id, newNode);
    this._rebuild();
  }

  replaceAll(nodes: Array<Omit<BranchNode, "children">>): void {
    this.nodeIndex.clear();
    for (const node of nodes) {
      this.nodeIndex.set(node.id, { ...node, children: [] });
    }
    this._rebuild();
  }

  private _pruneIndex(node: BranchNode): void {
    this.nodeIndex.delete(node.id);
    for (const child of node.children) this._pruneIndex(child);
  }

  private _rebuild(): void {
    this.roots = [];
    const nodes = Array.from(this.nodeIndex.values());
    for (const node of nodes) {
      node.children = [];
    }
    for (const node of nodes) {
      if (node.parentId) {
        const parent = this.nodeIndex.get(node.parentId);
        if (parent) {
          parent.children.push(node);
          continue;
        }
      }
      this.roots.push(node);
    }
    if (this.roots.length <= this.MAX_ROOTS) {
      return;
    }
    const removed = this.roots.splice(0, this.roots.length - this.MAX_ROOTS);
    for (const root of removed) {
      this._pruneIndex(root);
    }
    this.roots = Array.from(this.nodeIndex.values()).filter((node) => {
      return !node.parentId || !this.nodeIndex.has(node.parentId);
    });
  }

  render(buf: string[]): void {
    const { top, left, width, height } = this.rect;

    buf.push(moveTo(top, left) + chalk.bold.cyan(padEnd(" Branch Tree", width)));
    buf.push(moveTo(top + 1, left) + chalk.dim("─".repeat(width)));

    const lines: string[] = [];
    for (const root of this.roots.slice(-20)) {
      this._collectLines(root, "", true, lines);
    }

    const bodyHeight = height - 2;
    const visible = lines.slice(-bodyHeight);

    for (let i = 0; i < bodyHeight; i++) {
      const rowNum = top + 2 + i;
      const line = visible[i] ?? "";
      buf.push(moveTo(rowNum, left) + truncate(padEnd(line, width), width));
    }
  }

  private _collectLines(node: BranchNode, prefix: string, isLast: boolean, out: string[]): void {
    const connector = isLast ? "└─" : "├─";
    const icon = STATUS_ICON[node.status];
    const dur = node.durationMs != null ? chalk.dim(` ${node.durationMs}ms`) : "";
    const label = chalk.white(node.label);

    out.push(`${chalk.dim(prefix + connector)} ${icon} ${label}${dur}`);

    const childPrefix = prefix + (isLast ? "   " : "│  ");
    for (let i = 0; i < node.children.length; i++) {
      const child = node.children[i]!;
      this._collectLines(child, childPrefix, i === node.children.length - 1, out);
    }
  }
}
