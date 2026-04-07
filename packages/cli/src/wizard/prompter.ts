/**
 * wizard/prompter.ts — Interface e implementação readline para o wizard de onboarding.
 *
 * Porta fiel de rlm/cli/wizard/prompter.py + rich_prompter.py
 * Usa readline nativo do Node.js (zero dependências extras).
 */

import * as readline from "node:readline";
import {
  ANSI,
  StreamWriter,
  clearActiveProgressLine,
  registerActiveProgressLine,
  renderNote,
  restoreTerminalState,
  sanitizeForTerminal,
  stylePromptHint,
  stylePromptMessage,
  stylePromptTitle,
  unregisterActiveProgressLine,
  wrapWords,
  getTheme,
} from "@arkhe/terminal";

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

function cleanTerminalText(text: string): string {
  return sanitizeForTerminal(cleanMarkup(text));
}

function wrapNoteMessage(message: string, width: number): string {
  const innerWidth = Math.max(4, width - 4);
  return cleanTerminalText(message)
    .split("\n")
    .flatMap((line) => wrapWords(line, innerWidth))
    .join("\n");
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
    process.stdout.write(msg + "\n");
  }

  intro(title: string): void {
    this.println();
    this.println(SEP60);
    this.println(`  ${stylePromptTitle(cleanTerminalText(title)) ?? cleanTerminalText(title)}`);
    this.println(SEP60);
    this.println();
  }

  outro(message: string): void {
    this.println();
    this.println(DASH60);
    this.println(`  ${stylePromptTitle(cleanTerminalText(message)) ?? cleanTerminalText(message)}`);
    this.println();
  }

  note(message: string, title = ""): void {
    const width = Math.max(48, Math.min(88, (process.stdout.columns ?? 80) - 2));
    const noteOptions = title
      ? {
          title: cleanTerminalText(title),
          width,
        }
      : { width };
    this.println(
      renderNote("info", wrapNoteMessage(message, width), noteOptions),
    );
  }

  async select<T>(
    message: string,
    options: SelectOption<T>[],
    initialValue?: T,
  ): Promise<T> {
    this.println();
    this.println(stylePromptMessage(cleanTerminalText(message)));
    for (let i = 0; i < options.length; i++) {
      const opt = options[i];
      if (!opt) {
        continue;
      }
      const hint = opt.hint ? ` ${stylePromptHint(`(${cleanTerminalText(opt.hint)})`) ?? ""}` : "";
      this.println(`  ${i + 1}) ${cleanTerminalText(opt.label)}${hint}`);
    }

    let defaultIdx = "1";
    if (initialValue !== undefined) {
      const idx = options.findIndex((o) => o.value === initialValue);
      if (idx >= 0) defaultIdx = String(idx + 1);
    }

    while (true) {
      let raw: string;
      try {
        raw = await this.ask(`${stylePromptMessage("  Escolha")} [${defaultIdx}]: `);
      } catch {
        throw new WizardCancelledError();
      }
      const choice = raw.trim() || defaultIdx;
      const num = parseInt(choice, 10);
      if (!isNaN(num) && num >= 1 && num <= options.length) {
        const selected = options[num - 1];
        if (selected) {
          return selected.value;
        }
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
    const promptLabel = stylePromptMessage(cleanTerminalText(message));

    while (true) {
      let raw: string;
      try {
        raw = await this.ask(`${promptLabel}${cleanTerminalText(hint)}${suffix}: `);
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
      raw = await this.ask(`${stylePromptMessage(cleanTerminalText(message))} [${hint}]: `);
    } catch {
      throw new WizardCancelledError();
    }
    const v = raw.trim().toLowerCase();
    if (!v) return defaultValue;
    return ["s", "sim", "y", "yes"].includes(v);
  }

  progress(label: string): ProgressHandle {
    const writer = new StreamWriter(process.stdout);
    const render = (message: string, icon: string) => {
      const line = `${getTheme().info}${icon}${ANSI.RESET} ${stylePromptMessage(cleanTerminalText(message))}`;
      if (process.stdout.isTTY) {
        registerActiveProgressLine(process.stdout);
      }
      writer.rewrite(line);
    };

    render(label, "⏳");

    return {
      update: (msg: string) => render(msg, "…"),
      stop: (msg = "") => {
        clearActiveProgressLine();
        unregisterActiveProgressLine(process.stdout);
        if (msg) {
          void writer.writeLine(`${getTheme().success}✓${ANSI.RESET} ${cleanTerminalText(msg)}`);
        }
      },
    };
  }

  private ask(prompt: string): Promise<string> {
    return new Promise((resolve, reject) => {
      const rl = this.getRL();
      let settled = false;

      const finish = (fn: () => void) => {
        if (settled) {
          return;
        }
        settled = true;
        rl.off("close", onClose);
        process.off("SIGINT", onSigint);
        fn();
      };

      const onClose = () => {
        finish(() => {
          restoreTerminalState("wizard close", { resumeStdinIfPaused: true });
          reject(new WizardCancelledError());
        });
      };

      const onSigint = () => {
        finish(() => {
          restoreTerminalState("wizard interrupt", { resumeStdinIfPaused: true });
          rl.close();
          reject(new WizardCancelledError());
        });
      };

      rl.once("close", onClose);
      process.once("SIGINT", onSigint);
      rl.question(prompt, (answer) => finish(() => resolve(answer)));
    });
  }

  close(): void {
    restoreTerminalState("wizard cleanup", { resumeStdinIfPaused: true });
    this.rl?.close();
    this.rl = null;
  }
}

// ---------------------------------------------------------------------------
// Singleton padrão
// ---------------------------------------------------------------------------

export const defaultPrompter: WizardPrompter = new NodePrompter();
