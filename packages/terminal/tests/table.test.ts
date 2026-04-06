import { describe, it, expect } from "vitest";
import { renderTable, renderRow } from "../src/table.js";
import { strip, ANSI } from "../src/ansi.js";

describe("renderRow()", () => {
  it("renders cells separated by │ pipes", () => {
    expect(renderRow(["foo", "bar"], [3, 3])).toBe("│ foo │ bar │");
  });

  it("pads a short cell to column width", () => {
    // width=5, 'hi' is 2 wide → 3 trailing spaces + 1 space before │
    expect(renderRow(["hi"], [5])).toBe("│ hi    │");
  });

  it("truncates a cell that exceeds column width", () => {
    const result = strip(renderRow(["helloworld"], [5]));
    expect(result).toContain("hell…");
  });

  it("handles ANSI-colored cells by measuring visible width", () => {
    const colored = ANSI.fg(0, 188, 212) + "AB" + ANSI.RESET;
    const row = renderRow([colored], [4]);
    // Visible content is "AB  " (2 chars + 2 spaces), surrounded by pipes
    expect(strip(row)).toBe("│ AB   │");
  });

  it("handles empty cells", () => {
    const result = renderRow(["", "x"], [3, 1]);
    expect(result).toBe("│     │ x │");
  });

  it("handles a single-char width column", () => {
    const result = strip(renderRow(["abc"], [1]));
    expect(result).toBe("│ … │");
  });
});

describe("renderTable()", () => {
  it("renders headers and data rows with single border", () => {
    const result = renderTable({
      headers: ["Name", "Age"],
      rows: [
        ["Alice", "30"],
        ["Bob", "25"],
      ],
    });
    expect(result).toContain("Name");
    expect(result).toContain("Age");
    expect(result).toContain("Alice");
    expect(result).toContain("Bob");
  });

  it("opens and closes with correct single-border characters", () => {
    const result = renderTable({ headers: ["X"], rows: [] });
    expect(result).toContain("┌");
    expect(result).toContain("┘");
  });

  it("renders with style 'none' — no border pipes", () => {
    const result = renderTable({
      headers: ["A", "B"],
      rows: [["1", "2"]],
      borderStyle: "none",
    });
    expect(result).not.toContain("│");
    expect(result).toContain("A");
    expect(result).toContain("1");
  });

  it("renders with double border characters", () => {
    const result = renderTable({
      headers: ["X"],
      rows: [["y"]],
      borderStyle: "double",
    });
    expect(result).toContain("╔");
    expect(result).toContain("╚");
  });

  it("renders multiple columns and rows", () => {
    const result = renderTable({
      headers: ["ID", "Name", "Status"],
      rows: [
        ["1", "alpha", "active"],
        ["2", "beta", "inactive"],
      ],
    });
    expect(result).toContain("alpha");
    expect(result).toContain("inactive");
  });

  it("empty rows renders only header block", () => {
    const result = renderTable({ headers: ["Service", "Status"], rows: [] });
    const stripped = strip(result);
    expect(stripped).toContain("Service");
    expect(stripped).toContain("Status");
    // No data separator lines that aren't the header sep
    expect(result.split("\n").length).toBeLessThanOrEqual(5);
  });

  it("respects maxWidth by compressing column widths", () => {
    const result = strip(
      renderTable({
        headers: ["VeryLongHeaderName"],
        rows: [["VeryLongCellContent"]],
        maxWidth: 20,
      }),
    );
    for (const line of result.split("\n")) {
      expect(line.length).toBeLessThanOrEqual(20);
    }
  });

  it("missing cells are treated as empty string", () => {
    const result = renderTable({
      headers: ["A", "B", "C"],
      rows: [["only-a"]],
    });
    expect(result).toContain("only-a");
  });
});
