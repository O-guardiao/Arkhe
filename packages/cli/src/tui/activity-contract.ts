import type { BranchNode } from "./branch-tree.js";
import {
  normalizeActivityPayload,
  type ActivityBranchSnapshot,
  type ActivityControlsSnapshot,
  type ActivityCurrentTaskSnapshot,
  type ActivityGatewayEvent,
  type ActivityParallelSummarySnapshot,
  type ActivityRecord,
  type ActivitySessionSnapshot,
  type NormalizedActivityPayload,
} from "../lib/runtime-activity.js";

export {
  normalizeActivityPayload,
  type ActivityBranchSnapshot,
  type ActivityControlsSnapshot,
  type ActivityCurrentTaskSnapshot,
  type ActivityGatewayEvent,
  type ActivityParallelSummarySnapshot,
  type ActivityRecord,
  type ActivitySessionSnapshot,
  type NormalizedActivityPayload,
} from "../lib/runtime-activity.js";

export function toBranchTreeNodes(branches: ActivityBranchSnapshot[]): Array<Omit<BranchNode, "children">> {
  return branches.map((branch) => {
    const node: Omit<BranchNode, "children"> = {
      id: branch.id,
      label: branch.label,
      status: branch.surfaceStatus,
    };
    if (branch.parentId !== undefined) {
      node.parentId = branch.parentId;
    }
    if (branch.durationMs !== undefined) {
      node.durationMs = branch.durationMs;
    }
    return node;
  });
}
