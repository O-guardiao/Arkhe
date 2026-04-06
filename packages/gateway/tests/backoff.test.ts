import { describe, it, expect } from "vitest";
import { ExponentialBackoff, withBackoff } from "../src/backoff.js";

describe("ExponentialBackoff", () => {
  it("primeiro valor é initialMs ± 20% jitter", () => {
    const b = new ExponentialBackoff({ initialMs: 1000, jitter: true });
    const v = b.next();
    expect(v).toBeGreaterThanOrEqual(800);
    expect(v).toBeLessThanOrEqual(1200);
  });

  it("respeita maxMs", () => {
    const b = new ExponentialBackoff({ initialMs: 1000, maxMs: 3000, multiplier: 10 });
    b.next(); // 1000
    const v = b.next(); // 10000 → clampado a 3000 ± jitter
    expect(v).toBeLessThanOrEqual(3000 * 1.2); // 20% jitter máx
  });

  it("reset volta ao estado inicial", () => {
    const b = new ExponentialBackoff({ initialMs: 500, maxMs: 100_000 });
    b.next();
    b.next();
    b.next();
    b.reset();
    expect(b.currentAttempt).toBe(0);
    const v = b.next();
    expect(v).toBeGreaterThanOrEqual(400);
    expect(v).toBeLessThanOrEqual(600);
  });
});

describe("withBackoff", () => {
  it("retorna valor quando fn tem sucesso na primeira tentativa", async () => {
    const result = await withBackoff(() => Promise.resolve(42), 3);
    expect(result).toBe(42);
  });

  it("retenta e retorna sucesso na segunda tentativa", async () => {
    let attempts = 0;
    const result = await withBackoff(
      () => {
        attempts++;
        if (attempts < 2) throw new Error("falhou");
        return Promise.resolve("ok");
      },
      3,
      { initialMs: 1 },
    );
    expect(result).toBe("ok");
    expect(attempts).toBe(2);
  });

  it("lança após esgotar tentativas", async () => {
    await expect(
      withBackoff(
        () => { throw new Error("sempre falha"); },
        3,
        { initialMs: 1 },
      ),
    ).rejects.toThrow("sempre falha");
  });
});
