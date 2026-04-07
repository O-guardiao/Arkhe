import { describe, expect, it } from "vitest";
import { normalizeActivityPayload } from "../lib/runtime-activity.js";

describe("activity-contract", () => {
  it("normalizes branch lineage, duration and status from the Python payload", () => {
    const payload = normalizeActivityPayload({
      session: {
        session_id: "sess-1",
        client_id: "tui:test",
        status: "running",
      },
      runtime: {
        recursion: {
          controls: {
            paused: false,
            focused_branch_id: 1,
            fixed_winner_branch_id: 1,
            branch_priorities: { "2": 9 },
            last_checkpoint_path: "/tmp/cp-1",
            last_operator_note: "seguir ramo 1",
          },
          summary: {
            winner_branch_id: 1,
            cancelled_count: 1,
            failed_count: 2,
            total_tasks: 4,
            branch_count: 2,
            branch_status_counts: {
              completed: 1,
              blocked: 1,
            },
            strategy: { coordination_policy: "stop_on_solution" },
            stop_evaluation: { reason: "winner selected" },
          },
          events: [
            {
              operation: "fanout",
              topic: "branch/start",
              sender_id: 1,
              payload_preview: "2 branches",
              timestamp: "2026-04-07T10:00:00Z",
            },
          ],
        },
        coordination: {
          latest_parallel_summary: {
            winner_branch_id: 1,
            cancelled_count: 1,
            failed_count: 2,
            total_tasks: 4,
          },
          branch_tasks: [
            {
              branch_id: 1,
              title: "root",
              mode: "parallel",
              status: "completed",
              duration_ms: 250,
            },
            {
              branch_id: 2,
              title: "child",
              mode: "serial",
              status: "blocked",
              metadata: {
                parent_branch_id: 1,
                child_depth: 2,
                elapsed_s: 1.2,
                error: "timeout after 1.2s",
              },
            },
          ],
        },
      },
    });

    expect(payload.runtime.summary.winnerBranchId).toBe(1);
    expect(payload.runtime.summary.cancelledCount).toBe(1);
    expect(payload.runtime.summary.failedCount).toBe(2);
    expect(payload.runtime.summary.totalTasks).toBe(4);
    expect(payload.runtime.summary.branchCount).toBe(2);
    expect(payload.runtime.summary.branchStatusCounts["completed"]).toBe(1);
    expect(payload.runtime.summary.strategy["coordination_policy"]).toBe("stop_on_solution");

    expect(payload.runtime.controls.fixedWinnerBranchId).toBe(1);
    expect(payload.runtime.controls.branchPriorities["2"]).toBe(9);
    expect(payload.runtime.coordinationEvents[0]?.operation).toBe("fanout");
    expect(payload.runtime.coordinationEvents[0]?.payloadPreview).toBe("2 branches");

    expect(payload.runtime.branchTasks).toHaveLength(2);
    expect(payload.runtime.branchTasks[0]?.surfaceStatus).toBe("ok");

    const child = payload.runtime.branchTasks[1];
    expect(child?.parentId).toBe("1");
    expect(child?.depth).toBe(2);
    expect(child?.durationMs).toBe(1200);
    expect(child?.surfaceStatus).toBe("error");
    expect(child?.errorMessage).toContain("timeout");
  });
});