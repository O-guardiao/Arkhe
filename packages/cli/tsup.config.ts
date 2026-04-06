import { defineConfig } from "tsup";
import { builtinModules } from "node:module";

const nodeBuiltins = [
  ...builtinModules,
  ...builtinModules.map((m) => `node:${m}`),
];

export default defineConfig({
  entry: ["src/index.ts"],
  format: ["esm"],
  dts: true,
  clean: true,
  external: nodeBuiltins,
});
