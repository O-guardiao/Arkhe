import { describe, expect, it } from "vitest";
import { BranchTree } from "./branch-tree.js";

describe("BranchTree", () => {
  it("rebuilds hierarchy from the authoritative snapshot", () => {
    const tree = new BranchTree({ top: 1, left: 1, width: 80, height: 10 });

    tree.replaceAll([
      { id: "2", parentId: "1", label: "child", status: "running" },
      { id: "1", label: "root", status: "ok" },
    ]);

    const roots = (tree as unknown as { roots: Array<{ id: string; children: Array<{ id: string }> }> }).roots;
    expect(roots).toHaveLength(1);
    expect(roots[0]?.id).toBe("1");
    expect(roots[0]?.children[0]?.id).toBe("2");
  });
});