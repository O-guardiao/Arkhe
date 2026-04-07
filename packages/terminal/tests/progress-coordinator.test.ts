import { describe, expect, it, vi } from "vitest";
import {
  clearActiveProgressLine,
  registerActiveProgressLine,
  unregisterActiveProgressLine,
} from "../src/progress-coordinator.js";

function makeStream(isTTY: boolean) {
  return {
    isTTY,
    write: vi.fn(),
  } as unknown as NodeJS.WriteStream & { write: ReturnType<typeof vi.fn> };
}

describe("progress coordinator", () => {
  it("clears the active line only for tty streams", () => {
    const ttyStream = makeStream(true);
    registerActiveProgressLine(ttyStream);

    clearActiveProgressLine();

    expect(ttyStream.write).toHaveBeenCalledWith("\r\x1b[2K");
    unregisterActiveProgressLine(ttyStream);
  });

  it("ignores unregister requests from another stream", () => {
    const active = makeStream(true);
    const other = makeStream(true);
    registerActiveProgressLine(active);

    unregisterActiveProgressLine(other);
    clearActiveProgressLine();

    expect(active.write).toHaveBeenCalledTimes(1);
    unregisterActiveProgressLine(active);
  });
});