import { execFile } from "node:child_process";

export type ExecResult = {
  stdout: string;
  stderr: string;
  code: number;
};

/**
 * Wraps `child_process.execFile` with UTF-8 output and NO shell expansion.
 * Shell is always disabled to prevent command injection.
 */
export async function execFileUtf8(
  command: string,
  args: string[],
  options: {
    cwd?: string;
    env?: NodeJS.ProcessEnv;
    timeout?: number;
    maxBuffer?: number;
  } = {},
): Promise<ExecResult> {
  return new Promise<ExecResult>((resolve) => {
    execFile(
      command,
      args,
      { ...options, encoding: "utf8", shell: false },
      (error, stdout, stderr) => {
        if (!error) {
          resolve({
            stdout: String(stdout ?? ""),
            stderr: String(stderr ?? ""),
            code: 0,
          });
          return;
        }

        const e = error as { code?: unknown; message?: unknown };
        const stderrText = String(stderr ?? "");
        resolve({
          stdout: String(stdout ?? ""),
          stderr:
            stderrText ||
            (typeof e.message === "string" ? e.message : String(error)),
          code: typeof e.code === "number" ? e.code : 1,
        });
      },
    );
  });
}
