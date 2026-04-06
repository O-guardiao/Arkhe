/**
 * service-update.ts — Atualização do RLM via git + pnpm.
 *
 * Porta fiel de rlm/cli/service_update.py
 * Usa pnpm (em vez de uv) como gerenciador de dependências do lado TypeScript.
 */

import {
  existsSync,
} from "node:fs";
import { join, resolve, dirname } from "node:path";
import { spawnSync } from "node:child_process";
import { CliContext } from "./context.js";

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function looksLikeCheckout(path: string): boolean {
  return existsSync(join(path, ".git"));
}

function walkToCheckoutRoot(start: string): string | null {
  let current = resolve(start);
  while (true) {
    if (looksLikeCheckout(current)) return current;
    const parent = dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return null;
}

function packageCheckoutRoot(): string {
  // __dirname aponta para packages/cli/src — subir 3 níveis => raiz do repo
  return resolve(__dirname, "..", "..", "..");
}

function resolveProjectRoot(context: CliContext, targetPath?: string): string | null {
  const seen = new Set<string>();
  const candidates: string[] = [];

  function addCandidate(p: string | undefined): void {
    if (!p) return;
    const r = resolve(p);
    if (seen.has(r)) return;
    seen.add(r);
    candidates.push(r);
  }

  if (targetPath) addCandidate(targetPath);
  addCandidate(context.paths.projectRoot);
  addCandidate(packageCheckoutRoot());

  const repoDir = (context.env["ARKHE_REPO_DIR"] ?? "").trim();
  if (repoDir) addCandidate(repoDir);

  const installDir = (context.env["ARKHE_INSTALL_DIR"] ?? "").trim();
  if (installDir) addCandidate(join(installDir, "repo"));

  addCandidate(join(context.home, ".arkhe", "repo"));

  for (const candidate of candidates) {
    const root = walkToCheckoutRoot(candidate);
    if (root !== null) return root;
  }
  return null;
}

// ---------------------------------------------------------------------------
// updateInstallation
// ---------------------------------------------------------------------------

export interface UpdateCallbacks {
  ok: (msg: string) => void;
  err: (msg: string) => void;
  info: (msg: string) => void;
}

export async function updateInstallation(
  context: CliContext,
  opts: {
    checkOnly?: boolean;
    restart?: boolean;
    targetPath?: string;
  },
  callbacks: UpdateCallbacks,
  hooks: {
    servicesAreRunning: () => boolean;
    stopServices: () => Promise<number>;
    startServices: () => Promise<number>;
  },
): Promise<number> {
  const { checkOnly = false, restart = false, targetPath } = opts;
  const { ok, err, info } = callbacks;

  const projectRoot = resolveProjectRoot(context, targetPath);
  if (projectRoot === null) {
    if (targetPath) {
      err(`Nenhum checkout git do Arkhe foi encontrado em '${targetPath}'.`);
    } else {
      err(
        "Nenhum checkout git do Arkhe foi encontrado. " +
        "Rode o comando dentro do repo, use --path ou instale em ~/.arkhe/repo.",
      );
    }
    return 1;
  }

  if (!context.hasTool("git")) {
    err("`git` não encontrado no PATH.");
    return 1;
  }

  info(`Usando checkout em ${projectRoot}`);
  info("Validando worktree local...");

  const status = spawnSync("git", ["status", "--porcelain"], {
    cwd: projectRoot, encoding: "utf8", timeout: 10_000,
  });
  if (status.status !== 0) {
    err((status.stderr || status.stdout || "Falha ao ler estado do git.").trim());
    return 1;
  }

  const hasLocalChanges = Boolean((status.stdout as string).trim());
  if (hasLocalChanges) {
    info("Mudanças locais detectadas — guardando com git stash...");
    const stash = spawnSync(
      "git", ["stash", "--include-untracked", "-m", "arkhe-update-autostash"],
      { cwd: projectRoot, encoding: "utf8", timeout: 15_000 },
    );
    if (stash.status !== 0) {
      err((stash.stderr || stash.stdout || "Falha ao fazer git stash.").trim());
      return 1;
    }
  }

  const branchResult = spawnSync("git", ["rev-parse", "--abbrev-ref", "HEAD"], {
    cwd: projectRoot, encoding: "utf8", timeout: 5000,
  });
  if (branchResult.status !== 0) {
    err((branchResult.stderr || branchResult.stdout || "Falha ao detectar branch atual.").trim());
    return 1;
  }
  const branch = (branchResult.stdout as string).trim();
  if (!branch || branch === "HEAD") {
    err("Branch atual inválida para update automático.");
    return 1;
  }

  info(`Buscando updates remotos para '${branch}'...`);
  const fetch = spawnSync("git", ["fetch", "origin", branch, "--quiet"], {
    cwd: projectRoot, encoding: "utf8", timeout: 30_000,
  });
  if (fetch.status !== 0) {
    err((fetch.stderr || fetch.stdout || "Falha no git fetch.").trim());
    return 1;
  }

  const revList = spawnSync(
    "git", ["rev-list", "--left-right", "--count", `HEAD...origin/${branch}`],
    { cwd: projectRoot, encoding: "utf8", timeout: 5000 },
  );
  if (revList.status !== 0) {
    err((revList.stderr || revList.stdout || "Falha ao comparar commits.").trim());
    return 1;
  }

  const counts = (revList.stdout as string).trim().split(/\s+/);
  if (counts.length !== 2) {
    err("Saída inesperada do git rev-list ao comparar atualizações.");
    return 1;
  }
  const aheadCount = parseInt(counts[0], 10);
  const behindCount = parseInt(counts[1], 10);

  function restoreStash(): void {
    if (!hasLocalChanges) return;
    info("Restaurando mudanças locais com git stash pop...");
    const pop = spawnSync("git", ["stash", "pop"], {
      cwd: projectRoot!, encoding: "utf8", timeout: 10_000,
    });
    if (pop.status !== 0) {
      err("Conflito ao restaurar mudanças locais. Resolva com: git stash show -p | git apply --3way");
      info("Suas mudanças estão salvas no stash. Use 'git stash list' para ver.");
    }
  }

  if (checkOnly) {
    if (behindCount === 0 && aheadCount === 0) {
      ok("Checkout já está sincronizado com origin.");
    } else if (behindCount === 0) {
      ok("Checkout local está à frente do remoto; nada para baixar.");
    } else {
      ok(`Há ${behindCount} commit(s) pendente(s) em origin/${branch}.`);
    }
    restoreStash();
    return 0;
  }

  if (behindCount === 0) {
    ok("Nenhuma atualização remota disponível.");
    restoreStash();
    return 0;
  }

  if (aheadCount > 0) {
    restoreStash();
    err(
      `Checkout local divergiu de origin/${branch} ` +
      `(${aheadCount} commit(s) à frente, ${behindCount} atrás). ` +
      "Faça rebase/merge manual antes do update.",
    );
    return 1;
  }

  info("Aplicando git pull --ff-only...");
  const pull = spawnSync("git", ["pull", "--ff-only", "origin", branch], {
    cwd: projectRoot, encoding: "utf8", timeout: 30_000,
  });
  if (pull.status !== 0) {
    err((pull.stderr || pull.stdout || "Falha no git pull.").trim());
    return 1;
  }
  ok(`Código atualizado: ${behindCount} commit(s) aplicados.`);

  // TypeScript: usa pnpm (em vez de uv)
  const pkgManager = context.hasTool("pnpm") ? "pnpm" : "npm";
  info(`Reinstalando dependências com ${pkgManager} install...`);
  const install = spawnSync(pkgManager, ["install"], {
    cwd: projectRoot, encoding: "utf8", timeout: 120_000,
    stdio: "inherit",
  });
  if (install.status !== 0) {
    err(`Falha no ${pkgManager} install.`);
    return 1;
  }
  ok("Dependências sincronizadas.");

  restoreStash();

  if (restart && hooks.servicesAreRunning()) {
    info("Reiniciando serviços do RLM...");
    const stopRc = await hooks.stopServices();
    if (stopRc !== 0) {
      err("Falha ao parar serviços antes do restart.");
      return stopRc;
    }
    const startRc = await hooks.startServices();
    if (startRc !== 0) {
      err("Falha ao iniciar serviços após o update.");
      return startRc;
    }
    ok("Serviços reiniciados.");
  } else if (restart) {
    info("Serviços não estavam ativos; nenhum restart necessário.");
  }

  return 0;
}
