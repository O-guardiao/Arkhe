import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { ServicePlatform } from "./types.js";

export type ServiceAuditEntry = {
  /** ISO-8601 timestamp. */
  timestamp: string;
  /** Action performed, e.g. `install`, `start`, `stop`. */
  action: string;
  /** Service name. */
  name: string;
  /** Platform the action ran on. */
  platform: ServicePlatform;
  /** Whether the action succeeded. */
  success: boolean;
  /** Error message if `success` is false. */
  error?: string;
};

/**
 * Append-only JSONL audit log for service lifecycle events.
 *
 * Each entry is a JSON-serialised `ServiceAuditEntry` followed by `\n`.
 * The file is created on first write.
 */
export class ServiceAudit {
  readonly #path: string;

  constructor(filePath?: string) {
    this.#path =
      filePath ??
      path.join(os.homedir(), ".rlm", "daemon", "audit.jsonl");
  }

  /** Returns the absolute path to the audit log file. */
  getPath(): string {
    return this.#path;
  }

  /**
   * Appends a single audit entry to the JSONL file.
   * Creates parent directories automatically.
   */
  async log(entry: ServiceAuditEntry): Promise<void> {
    const line = JSON.stringify(entry) + "\n";
    await fs.mkdir(path.dirname(this.#path), { recursive: true });
    await fs.appendFile(this.#path, line, "utf8");
  }

  /**
   * Returns the most recent `n` audit entries, in chronological order.
   * Returns an empty array if the file does not exist or cannot be parsed.
   */
  async getRecent(n: number): Promise<ServiceAuditEntry[]> {
    let content: string;
    try {
      content = await fs.readFile(this.#path, "utf8");
    } catch {
      return [];
    }

    const lines = content.split("\n").filter((l) => l.trim().length > 0);
    const tail = lines.slice(-n);
    const results: ServiceAuditEntry[] = [];

    for (const line of tail) {
      try {
        results.push(JSON.parse(line) as ServiceAuditEntry);
      } catch {
        // Skip malformed lines
      }
    }

    return results;
  }
}
