/**
 * Allowlist / blocklist para controle de acesso a canais.
 *
 * Uma lista de regras é avaliada em ordem. A primeira regra que corresponde
 * ao `id` define a decisão final (`allow` ou `deny`). Se nenhuma regra
 * corresponder, a política padrão é `deny`.
 *
 * Tipos de padrão suportados:
 *   - `exact`  — correspondência exata (case-sensitive)
 *   - `prefix` — `id` deve começar com o padrão
 *   - `regex`  — padrão é uma expressão regular
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// Schema Zod
// ---------------------------------------------------------------------------

export const AllowlistRuleSchema = z.object({
  pattern: z.string().min(1),
  type: z.enum(["exact", "prefix", "regex"]),
  action: z.enum(["allow", "deny"]),
});

export type AllowlistRule = z.infer<typeof AllowlistRuleSchema>;

// ---------------------------------------------------------------------------
// Funções públicas
// ---------------------------------------------------------------------------

/**
 * Avalia uma lista de regras contra um `id`.
 *
 * Retorna `allow` ou `deny`. Política padrão (sem match): `deny`.
 */
export function matchAllowlist(rules: AllowlistRule[], id: string): "allow" | "deny" {
  for (const rule of rules) {
    if (ruleMatches(rule, id)) {
      return rule.action;
    }
  }
  return "deny";
}

/**
 * Cria uma função de verificação compilada a partir de uma lista de regras.
 *
 * @returns `true` se o `id` é permitido, `false` caso contrário
 */
export function createAllowlist(rules: AllowlistRule[]): (id: string) => boolean {
  return (id: string) => matchAllowlist(rules, id) === "allow";
}

// ---------------------------------------------------------------------------
// Helpers internos
// ---------------------------------------------------------------------------

function ruleMatches(rule: AllowlistRule, id: string): boolean {
  switch (rule.type) {
    case "exact":
      return rule.pattern === id;
    case "prefix":
      return id.startsWith(rule.pattern);
    case "regex":
      return new RegExp(rule.pattern).test(id);
  }
}
