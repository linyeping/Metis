import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { foregroundTargetsAreIdle } from "../src/foreground-lifecycle.js";

describe("tools-dev foreground lifecycle", () => {
  it("exits only after every managed sidecar is idle", () => {
    assert.equal(foregroundTargetsAreIdle([]), false);
    assert.equal(foregroundTargetsAreIdle([{ state: "running" }, { state: "idle" }]), false);
    assert.equal(foregroundTargetsAreIdle([{ state: "idle" }, { state: "stopped" }]), true);
  });
});
