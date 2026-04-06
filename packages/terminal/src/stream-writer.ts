import { ANSI } from "./ansi.js";

/**
 * Async-safe wrapper around a `NodeJS.WriteStream` (e.g. `process.stdout`).
 *
 * Provides Promise-based `write` / `writeLine` for normal output and
 * synchronous cursor-control helpers (`clearLine`, `moveUp`, `rewrite`)
 * for live progress updates.
 */
export class StreamWriter {
  private readonly stream: NodeJS.WriteStream;

  constructor(stream: NodeJS.WriteStream) {
    this.stream = stream;
  }

  /**
   * Write `text` and resolve when the data has been flushed.
   * Rejects on stream errors.
   */
  write(text: string): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      this.stream.write(text, (err?: Error | null) => {
        if (err != null) reject(err);
        else resolve();
      });
    });
  }

  /** Write `text` followed by a newline. */
  writeLine(text: string): Promise<void> {
    return this.write(text + "\n");
  }

  /**
   * Clear the current terminal line (no-op on non-TTY streams).
   * Uses CR + CLEAR_LINE so the cursor returns to column 1.
   */
  clearLine(): void {
    if (this.stream.isTTY) {
      this.stream.write("\r" + ANSI.CLEAR_LINE);
    }
  }

  /**
   * Move the cursor up `n` lines in TTY mode.
   * No-op on non-TTY streams.
   */
  moveUp(n: number): void {
    if (this.stream.isTTY) {
      this.stream.write(ANSI.UP(n));
    }
  }

  /**
   * Clear the current line and write `text` in its place.
   *
   * In TTY mode the cursor stays on the same line (useful for live
   * progress bars).  In non-TTY mode a newline is appended instead so
   * the output is still readable in pipes / log files.
   */
  rewrite(text: string): void {
    if (this.stream.isTTY) {
      this.stream.write("\r" + ANSI.CLEAR_LINE + text);
    } else {
      this.stream.write(text + "\n");
    }
  }
}
