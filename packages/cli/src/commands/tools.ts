/**
 * Comando `rlm tools` — lista e executa ferramentas do brain.
 *
 * Uso:
 *   rlm tools list
 *   rlm tools exec bash --input '{"command": "echo hello"}'
 */

import { Command } from "commander";
import ora from "ora";
import { RlmClient } from "../client.js";
import { c, fmtMs, printError, printJson, printTable } from "../format.js";

interface ToolListItem {
  name: string;
  description: string;
  layer: string;
  required_permission: string;
}

interface ExecResult {
  tool_name: string;
  success: boolean;
  result: unknown;
  error: string | null;
  elapsed_ms: number;
  denied: boolean;
  requires_approval: boolean;
}

export function makeToolsCommand(): Command {
  const tools = new Command("tools").description("Gerencia ferramentas do brain");

  // --- list ---
  tools
    .command("list")
    .description("Lista ferramentas disponíveis no registry")
    .option("--layer <layer>", "Filtrar por layer (builtin|plugin|runtime)")
    .option("--perm <perm>", "Filtrar por permissão requerida")
    .option("--json", "Saída em JSON bruto", false)
    .action(async (opts: { layer?: string; perm?: string; json: boolean }) => {
      const client = new RlmClient();
      try {
        let items = await client.get<ToolListItem[]>("/brain/tools");

        if (opts.layer) {
          items = items.filter((t) => t.layer === opts.layer);
        }
        if (opts.perm) {
          items = items.filter((t) => t.required_permission === opts.perm);
        }

        if (opts.json) {
          printJson(items);
          return;
        }

        if (items.length === 0) {
          console.log(c.dim("Nenhuma ferramenta encontrada."));
          return;
        }

        printTable(
          items.map((t) => ({
            nome: t.name,
            layer: t.layer,
            permissão: t.required_permission,
            descrição: t.description.slice(0, 60),
          })),
          ["nome", "layer", "permissão", "descrição"]
        );
        console.log(c.dim(`\n${items.length} ferramenta(s) encontrada(s).`));
      } catch (err) {
        printError(err);
        process.exit(1);
      }
    });

  // --- exec ---
  tools
    .command("exec <tool-name>")
    .description("Executa uma ferramenta diretamente")
    .option("-i, --input <json>", "Inputs em JSON", "{}")
    .option("-a, --actor <actor>", "Actor da chamada", "cli")
    .option("--json", "Saída em JSON bruto", false)
    .action(async (toolName: string, opts: { input: string; actor: string; json: boolean }) => {
      let inputs: Record<string, unknown>;
      try {
        inputs = JSON.parse(opts.input) as Record<string, unknown>;
      } catch {
        console.error(c.error("--input precisa ser um JSON válido."));
        process.exit(1);
      }

      const client = new RlmClient();
      const spinner = opts.json ? null : ora(c.dim(`Executando ${c.tool(toolName)}…`)).start();

      try {
        const res = await client.post<ExecResult>(`/brain/exec/${toolName}`, {
          inputs,
          actor: opts.actor,
        });

        spinner?.stop();

        if (opts.json) {
          printJson(res);
          return;
        }

        if (res.denied) {
          console.log(c.error(`[NEGADO] Ferramenta "${toolName}" bloqueada pela política.`));
          process.exit(1);
        }

        if (res.requires_approval) {
          console.log(c.warn(`[APROVAÇÃO NECESSÁRIA] Ferramenta "${toolName}" aguarda confirmação.`));
          process.exit(2);
        }

        if (!res.success) {
          console.log(c.error(`Falha: ${res.error ?? "erro desconhecido"}`));
          process.exit(1);
        }

        console.log(c.success(`✓ ${toolName}`) + c.dim(` (${fmtMs(res.elapsed_ms)})`));
        console.log();

        if (typeof res.result === "string") {
          console.log(res.result);
        } else {
          printJson(res.result);
        }
      } catch (err) {
        spinner?.fail("Falha ao executar ferramenta");
        printError(err);
        process.exit(1);
      }
    });

  return tools;
}
