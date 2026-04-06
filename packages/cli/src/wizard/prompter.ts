/**
 * wizard/prompter.ts — Interface e implementação readline para o wizard de onboarding.
 *
 * Porta fiel de rlm/cli/wizard/prompter.py + rich_prompter.py
 * Usa readline nativo do Node.js (zero dependências extras).
 */

import * as readline from "node:readline";

// ---------------------------------------------------------------------------
// WizardCancelledError
// ---------------------------------------------------------------------------

export class WizardCancelledError extends Error {
  constructor(message = "wizard cancelado") {
    super(message);
    this.name = "WizardCancelledError";
  }
}

// ---------------------------------------------------------------------------
// Interfaces públicas
// ---------------------------------------------------------------------------

export interface SelectOption<T = unknown> {
  value: T;
  label: string;
  hint?: string;
}

export interface ProgressHandle {
  update(msg: string): void;
  stop(msg?: string): void;
}

/** Contrato de I/O para todas as interações do wizard. */
export interface WizardPrompter {
  intro(title: string): void;
  outro(message: string): void;
  note(message: string, title?: string): void;
  select<T>(message: string, options: SelectOption<T>[], initialValue?: T): Promise<T>;
  text(opts: {
    message: string;
    default?: string;
    placeholder?: string;
    password?: boolean;
    validate?: (value: string) => string | undefined;
  }): Promise<string>;
  confirm(message: string, defaultValue?: boolean): Promise<boolean>;
  progress(label: string): ProgressHandle;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function cleanMarkup(text: string): string {
  return text.replace(/\[\/?\w[^\]]*\]/g, "");
}

const SEP60 = "═".repeat(60);
const DASH60 = "─".repeat(60);

// ---------------------------------------------------------------------------
// NodePrompter — implementação com readline + chalk opcionais
// ---------------------------------------------------------------------------

export class NodePrompter implements WizardPrompter {
  private rl: readline.Interface | null = null;

  private getRL(): readline.Interface {
    if (!this.rl) {
      this.rl = readline.createInterface({
        input: process.stdin,
        output: process.stdout,
        terminal: process.stdin.isTTY,
      });
    }
    return this.rl;
  }

  private println(msg = ""): void {
    process.stdout.write(cleanMarkup(msg) + "\n");
  }

  intro(title: string): void {
    this.println();
    this.println(SEP60);
    this.println(`  ${cleanMarkup(title)}`);
    this.println(SEP60);
    this.println();
  }

  outro(message: string): void {
    this.println();
    this.println(DASH60);
    this.println(`  ${cleanMarkup(message)}`);
    this.println();
  }

  note(message: string, title = ""): void {
    if (title) this.println(`\n[${cleanMarkup(title)}]`);
    this.println(cleanMarkup(message));
  }

  async select<T>(
    message: string,
    options: SelectOption<T>[],
    initialValue?: T,
  ): Promise<T> {
    this.println();
    this.println(cleanMarkup(message));
    for (let i = 0; i < options.length; i++) {
      const opt = options[i];
      const hint = opt.hint ? `  (${opt.hint})` : "";
      this.println(`  ${i + 1}) ${cleanMarkup(opt.label)}${hint}`);
    }

    let defaultIdx = "1";
    if (initialValue !== undefined) {
      const idx = options.findIndex((o) => o.value === initialValue);
      if (idx >= 0) defaultIdx = String(idx + 1);
    }

    while (true) {
      let raw: string;
      try {
        raw = await this.ask(`  Escolha [${defaultIdx}]: `);
      } catch {
        throw new WizardCancelledError();
      }
      const choice = raw.trim() || defaultIdx;
      const num = parseInt(choice, 10);
      if (!isNaN(num) && num >= 1 && num <= options.length) {
        return options[num - 1].value;
      }
      this.println("  Opção inválida. Tente novamente.");
    }
  }

  async text(opts: {
    message: string;
    default?: string;
    placeholder?: string;
    password?: boolean;
    validate?: (value: string) => string | undefined;
  }): Promise<string> {
    const { message, default: def = "", placeholder = "", validate } = opts;
    const hint = placeholder && !def ? ` (${placeholder})` : "";
    const suffix = def ? ` [${def}]` : "";

    while (true) {
      let raw: string;
      try {
        raw = await this.ask(`${cleanMarkup(message)}${hint}${suffix}: `);
      } catch {
        throw new WizardCancelledError();
      }
      const value = (raw.trim() || def).trim();
      if (validate) {
        const err = validate(value);
        if (err) {
          this.println(`  ${err}`);
          continue;
        }
      }
      return value;
    }
  }

  async confirm(message: string, defaultValue = true): Promise<boolean> {
    const hint = defaultValue ? "S/n" : "s/N";
    let raw: string;
    try {
      raw = await this.ask(`${cleanMarkup(message)} [${hint}]: `);
    } catch {
      throw new WizardCancelledError();
    }
    const v = raw.trim().toLowerCase();
    if (!v) return defaultValue;
    return ["s", "sim", "y", "yes"].includes(v);
  }

  progress(label: string): ProgressHandle {
    process.stdout.write(`  ⏳ ${label}\n`);
    return {
      update: (msg: string) => process.stdout.write(`  … ${msg}\n`),
      stop: (msg = "") => { if (msg) process.stdout.write(`${cleanMarkup(msg)}\n`); },
    };
  }

  private ask(prompt: string): Promise<string> {
    return new Promise((resolve, reject) => {
      const rl = this.getRL();
      rl.question(prompt, (answer) => resolve(answer));
      rl.once("close", () => reject(new WizardCancelledError()));
      process.once("SIGINT", () => {
        rl.close();
        reject(new WizardCancelledError());
      });
    });
  }

  close(): void {
    this.rl?.close();
    this.rl = null;
  }
}

// ---------------------------------------------------------------------------
// Singleton padrão
// ---------------------------------------------------------------------------

export const defaultPrompter: WizardPrompter = new NodePrompter();
