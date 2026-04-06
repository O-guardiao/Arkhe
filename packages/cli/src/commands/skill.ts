/**
 * Comando `rlm skill` — gerencia skills (ferramentas) do Brain.
 *
 * Subcomandos:
 *   rlm skill list                  — lista skills instaladas
 *   rlm skill install <name>        — instala uma skill
 *   rlm skill remove <name>         — remove uma skill
 *   rlm skill info <name>           — detalhes de uma skill
 */

import { Command } from "commander";
import { RlmClient } from "../client.js";
import { c, printTable, printError } from "../format.js";

interface SkillInfo {
  name: string;
  version: string;
  description: string;
  enabled: boolean;
  author?: string;
  tags?: string[];
}

export function makeSkillCommand(): Command {
  const cmd = new Command("skill")
    .aliases(["skills", "sk"])
    .description("Gerenciamento de skills (ferramentas) do Brain");

  // rlm skill list
  cmd
    .command("list")
    .aliases(["ls"])
    .description("Lista todas as skills instaladas no Brain")
    .option("--json", "Saída em JSON bruto", false)
    .option("--disabled", "Incluir skills desabilitadas", false)
    .action(async (opts: { json: boolean; disabled: boolean }) => {
      const client = new RlmClient();
      const data = await client.get<{ skills: SkillInfo[] }>("/brain/skills");
      let skills = data.skills ?? [];

      if (!opts.disabled) {
        skills = skills.filter((s) => s.enabled);
      }

      if (opts.json) {
        process.stdout.write(JSON.stringify(skills, null, 2) + "\n");
        return;
      }

      if (skills.length === 0) {
        process.stdout.write(c.warn("Nenhuma skill instalada.\n"));
        return;
      }

      printTable(
        ["Skill", "Versão", "Status", "Descrição"],
        skills.map((sk) => [
          c.bold(sk.name),
          sk.version,
          sk.enabled ? c.success("ativa") : c.warn("desabilitada"),
          sk.description.slice(0, 60),
        ]),
      );
    });

  // rlm skill install <name>
  cmd
    .command("install <name>")
    .description("Instala uma skill no Brain")
    .option("--version <ver>", "Versão específica")
    .action(async (name: string, opts: { version?: string }) => {
      const client = new RlmClient();
      process.stdout.write(`Instalando skill ${c.bold(name)}...`);
      try {
        await client.post("/brain/skills/install", { name, version: opts.version });
        process.stdout.write(` ${c.success("✓")}\n`);
      } catch (err) {
        process.stdout.write("\n");
        printError(`Falha ao instalar skill: ${String(err)}`);
        process.exit(1);
      }
    });

  // rlm skill remove <name>
  cmd
    .command("remove <name>")
    .aliases(["rm", "uninstall"])
    .description("Remove uma skill do Brain")
    .action(async (name: string) => {
      const client = new RlmClient();
      try {
        await client.post("/brain/skills/remove", { name });
        process.stdout.write(`${c.success("✓")} Skill ${c.bold(name)} removida.\n`);
      } catch (err) {
        printError(`Falha ao remover skill: ${String(err)}`);
        process.exit(1);
      }
    });

  // rlm skill info <name>
  cmd
    .command("info <name>")
    .description("Exibe detalhes de uma skill")
    .action(async (name: string) => {
      const client = new RlmClient();
      try {
        const skill = await client.get<SkillInfo>(`/brain/skills/${encodeURIComponent(name)}`);
        process.stdout.write(`\n`);
        process.stdout.write(`  ${c.bold("Nome")}        ${skill.name}\n`);
        process.stdout.write(`  ${c.bold("Versão")}      ${skill.version}\n`);
        process.stdout.write(`  ${c.bold("Status")}      ${skill.enabled ? c.success("ativa") : c.warn("desabilitada")}\n`);
        process.stdout.write(`  ${c.bold("Descrição")}   ${skill.description}\n`);
        if (skill.author) process.stdout.write(`  ${c.bold("Autor")}       ${skill.author}\n`);
        if (skill.tags?.length) process.stdout.write(`  ${c.bold("Tags")}        ${skill.tags.join(", ")}\n`);
        process.stdout.write(`\n`);
      } catch {
        printError(`Skill "${name}" não encontrada.`);
        process.exit(1);
      }
    });

  return cmd;
}
