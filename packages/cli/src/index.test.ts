import { describe, expect, it } from "vitest";
import { createProgram } from "./index.js";

describe("CLI operational contract", () => {
  it("keeps legacy top-level operational commands registered", () => {
    const program = createProgram();
    const commandNames = program.commands.map((command) => command.name());

    expect(commandNames).toEqual(
      expect.arrayContaining(["start", "stop", "status", "update", "tui", "workbench"]),
    );
  });

  it("keeps legacy update flags available", () => {
    const program = createProgram();
    const updateCommand = program.commands.find((command) => command.name() === "update");

    expect(updateCommand).toBeDefined();
    expect(updateCommand?.options.map((option) => option.long)).toEqual(
      expect.arrayContaining(["--check", "--no-restart", "--path"]),
    );
  });
});