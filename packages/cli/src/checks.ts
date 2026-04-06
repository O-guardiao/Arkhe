/**
 * checks.ts — Verificações de ambiente do CLI Arkhe.
 *
 * Porta fiel de rlm/cli/checks.py
 * Verifica requisitos de runtime antes de executar comandos.
 */

const MIN_NODE_MAJOR = 18;

// ---------------------------------------------------------------------------
// Node.js version check
// ---------------------------------------------------------------------------

/** Retorna [ok, mensagem] */
export function checkNodeVersion(): [boolean, string] {
  const rawVersion = process.version; // ex: "v20.11.0"
  const match = rawVersion.match(/^v(\d+)\.(\d+)\.(\d+)/);
  if (!match) {
    return [false, `Não foi possível detectar a versão do Node.js: ${rawVersion}`];
  }
  const major = parseInt(match[1], 10);
  if (major < MIN_NODE_MAJOR) {
    return [
      false,
      `Node.js ${MIN_NODE_MAJOR}+ é necessário. Versão atual: ${rawVersion}`,
    ];
  }
  return [true, `Node.js ${rawVersion} ✓`];
}

// ---------------------------------------------------------------------------
// require_supported_runtime — bloqueia comando se runtime não atender
// ---------------------------------------------------------------------------

/**
 * Verifica e imprime diagnóstico de runtime.
 * Retorna false e encerra com código 1 se o runtime não for suportado.
 */
export function requireSupportedRuntime(commandName: string): boolean {
  const checks: Array<{ name: string; fn: () => [boolean, string] }> = [
    { name: "Node.js", fn: checkNodeVersion },
  ];

  let allOk = true;
  for (const check of checks) {
    const [ok, msg] = check.fn();
    if (!ok) {
      console.error(`[${commandName}] Verificação falhou: ${msg}`);
      allOk = false;
    }
  }

  if (!allOk) {
    process.exit(1);
  }
  return true;
}

// ---------------------------------------------------------------------------
// doctor_runtime_requirement — resumo formatado (usado pelo comando doctor)
// ---------------------------------------------------------------------------

export interface RuntimeCheck {
  name: string;
  ok: boolean;
  message: string;
}

export function collectRuntimeChecks(): RuntimeCheck[] {
  const [nodeOk, nodeMsg] = checkNodeVersion();
  return [{ name: "Node.js", ok: nodeOk, message: nodeMsg }];
}
