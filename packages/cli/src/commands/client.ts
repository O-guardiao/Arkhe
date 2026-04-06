/**
 * Comando `rlm client` — gestão de dispositivos/clientes.
 *
 * Cada dispositivo (bot, ESP32, iPhone, PC) possui token próprio,
 * perfil e permissões. O token é exibido UMA ÚNICA VEZ no `add`.
 *
 * Subcomandos:
 *   rlm client add <id> [--profile] [--description] [--context] [--metadata]
 *   rlm client list [--all]
 *   rlm client status <id>
 *   rlm client revoke <id>
 *
 * Referência: docs/arquitetura-config-multidevice.md §7 (Camada 3)
 */

import { Command } from "commander";
import { RlmClient, RlmApiError } from "../client.js";
import { c, fmtBool, printError, printJson } from "../format.js";

// ---------------------------------------------------------------------------
// Tipos — espelham os schemas Pydantic do brain_router.py /admin/clients/*
// ---------------------------------------------------------------------------

interface ClientRecord {
  id: string;
  profile: string;
  active: boolean;
  description: string;
  context_hint: string;
  permissions: string[];
  created_at: string;
  last_seen: string | null;
  metadata: Record<string, unknown>;
}

interface ClientListResponse {
  clients: ClientRecord[];
  count: number;
}

interface ClientRegisterResponse {
  client_id: string;
  token: string;
  profile: string;
}

// ---------------------------------------------------------------------------
// Helpers de formatação
// ---------------------------------------------------------------------------

function activeLabel(active: boolean): string {
  return active ? c.success("ativo") : c.error("revogado");
}

function printClientTable(clients: ClientRecord[]): void {
  if (clients.length === 0) {
    console.log(c.dim("Nenhum cliente registrado."));
    return;
  }

  const COL_ID = 22;
  const COL_PROFILE = 12;
  const COL_STATUS = 10;
  const COL_SEEN = 22;

  const header = [
    c.bold("ID".padEnd(COL_ID)),
    c.bold("Profile".padEnd(COL_PROFILE)),
    c.bold("Status".padEnd(COL_STATUS)),
    c.bold("Último acesso".padEnd(COL_SEEN)),
    c.bold("Descrição"),
  ].join("  ");

  const sep = c.dim("-".repeat(90));

  console.log(header);
  console.log(sep);

  for (const cl of clients) {
    const line = [
      c.info(cl.id.padEnd(COL_ID)),
      cl.profile.padEnd(COL_PROFILE),
      activeLabel(cl.active).padEnd(COL_STATUS + 8), // +8 para escape de cores
      (cl.last_seen ?? "—").padEnd(COL_SEEN),
      c.dim(cl.description || "—"),
    ].join("  ");
    console.log(line);
  }
}

// ---------------------------------------------------------------------------
// Comando principal
// ---------------------------------------------------------------------------

export function makeClientCommand(): Command {
  const cmd = new Command("client")
    .aliases(["cl"])
    .description("Gerencia dispositivos/clientes registrados no sistema");

  // -------------------------------------------------------------------------
  // rlm client add <id>
  // -------------------------------------------------------------------------
  cmd
    .command("add <client-id>")
    .description("Registra novo dispositivo/cliente (token exibido uma única vez)")
    .option("-p, --profile <profile>", "Perfil do cliente", "default")
    .option("-d, --description <desc>", "Descrição livre", "")
    .option("--context <hint>", "Context hint para roteamento", "")
    .option(
      "--permissions <list>",
      "Permissões separadas por vírgula (ex: read_only,workspace_write)",
      "",
    )
    .option("--metadata <json>", "Metadata JSON adicional (ex: '{\"preferred_channel\":\"telegram\"}')", "{}")
    .option("--json", "Saída em JSON bruto", false)
    .action(
      async (
        clientId: string,
        opts: {
          profile: string;
          description: string;
          context: string;
          permissions: string;
          metadata: string;
          json: boolean;
        },
      ) => {
        let metadata: Record<string, unknown> = {};
        try {
          metadata = JSON.parse(opts.metadata) as Record<string, unknown>;
        } catch {
          console.error(c.error(`--metadata inválido (não é JSON): ${opts.metadata}`));
          process.exit(1);
        }

        const permissions = opts.permissions
          ? opts.permissions.split(",").map((p) => p.trim()).filter(Boolean)
          : [];

        const client = new RlmClient();
        try {
          const res = await client.post<ClientRegisterResponse>("/brain/admin/clients", {
            client_id: clientId,
            profile: opts.profile,
            description: opts.description,
            context_hint: opts.context,
            permissions,
            metadata,
          });

          if (opts.json) {
            printJson(res);
            return;
          }

          console.log(c.success(`\nCliente '${res.client_id}' criado (profile=${res.profile})`));
          console.log(`  Token: ${c.bold(res.token)}`);
          console.log(c.warn("  ⚠  Copie agora — não será exibido novamente.\n"));
        } catch (err) {
          if (err instanceof RlmApiError && err.statusCode === 409) {
            console.error(c.error(`Conflito: ${err.message}`));
          } else {
            printError(err);
          }
          process.exit(1);
        }
      },
    );

  // -------------------------------------------------------------------------
  // rlm client list
  // -------------------------------------------------------------------------
  cmd
    .command("list")
    .aliases(["ls"])
    .description("Lista clientes registrados")
    .option("-a, --all", "Incluir clientes revogados", false)
    .option("--json", "Saída em JSON bruto", false)
    .action(async (opts: { all: boolean; json: boolean }) => {
      const client = new RlmClient();
      try {
        const res = await client.get<ClientListResponse>(
          `/brain/admin/clients${opts.all ? "?include_inactive=true" : ""}`,
        );

        if (opts.json) {
          printJson(res);
          return;
        }

        console.log(c.dim(`\n${res.count} cliente(s) encontrado(s)\n`));
        printClientTable(res.clients);
        console.log();
      } catch (err) {
        printError(err);
        process.exit(1);
      }
    });

  // -------------------------------------------------------------------------
  // rlm client status <id>
  // -------------------------------------------------------------------------
  cmd
    .command("status <client-id>")
    .description("Exibe status detalhado de um cliente")
    .option("--json", "Saída em JSON bruto", false)
    .action(async (clientId: string, opts: { json: boolean }) => {
      const client = new RlmClient();
      try {
        const info = await client.get<ClientRecord>(`/brain/admin/clients/${encodeURIComponent(clientId)}`);

        if (opts.json) {
          printJson(info);
          return;
        }

        const status = activeLabel(info.active);
        console.log(`\n  ID:            ${c.info(info.id)}`);
        console.log(`  Status:        ${status}`);
        console.log(`  Profile:       ${info.profile}`);
        console.log(`  Descrição:     ${info.description || c.dim("—")}`);
        console.log(`  Context Hint:  ${info.context_hint || c.dim("—")}`);
        console.log(`  Permissões:    ${info.permissions.join(", ") || c.dim("nenhuma")}`);
        console.log(`  Criado em:     ${info.created_at}`);
        console.log(`  Último acesso: ${info.last_seen ?? c.dim("—")}`);
        if (Object.keys(info.metadata).length > 0) {
          console.log(`  Metadata:      ${JSON.stringify(info.metadata)}`);
        }
        console.log();
      } catch (err) {
        if (err instanceof RlmApiError && err.statusCode === 404) {
          console.error(c.error(`Cliente '${clientId}' não encontrado.`));
        } else {
          printError(err);
        }
        process.exit(1);
      }
    });

  // -------------------------------------------------------------------------
  // rlm client revoke <id>
  // -------------------------------------------------------------------------
  cmd
    .command("revoke <client-id>")
    .description("Revoga acesso de um cliente (mantém histórico para auditoria)")
    .option("--json", "Saída em JSON bruto", false)
    .action(async (clientId: string, opts: { json: boolean }) => {
      const client = new RlmClient();
      try {
        const res = await client.delete<{ client_id: string; revoked: boolean }>(
          `/brain/admin/clients/${encodeURIComponent(clientId)}`,
        );

        if (opts.json) {
          printJson(res);
          return;
        }

        console.log(c.success(`\nCliente '${res.client_id}' revogado com sucesso.\n`));
      } catch (err) {
        if (err instanceof RlmApiError && err.statusCode === 404) {
          console.error(c.error(`Cliente '${clientId}' não encontrado ou já revogado.`));
        } else {
          printError(err);
        }
        process.exit(1);
      }
    });

  return cmd;
}
