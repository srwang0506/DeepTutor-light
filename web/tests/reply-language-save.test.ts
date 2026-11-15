import assert from "node:assert/strict";
import test from "node:test";

import { waitForReplyLanguageSave } from "../features/chat/controllers/reply-language-save";

test("a failed selector save keeps the draft and blocks sending in the old language", async () => {
  const restored: string[] = [];
  const send = async (pending: Promise<void> | null) => {
    if (!(await waitForReplyLanguageSave(pending, "Explain in French", (draft) => restored.push(draft)))) {
      return false;
    }
    return true;
  };

  assert.equal(await send(Promise.reject(new Error("Save failed"))), false);
  assert.deepEqual(restored, ["Explain in French"]);
  assert.equal(await send(Promise.resolve()), true);
  assert.equal(await send(null), true);
});

test("does not resume a pending send in a different account, workspace, or conversation", async () => {
  for (const switchedAt of ["account", "workspace", "session"] as const) {
    let finish!: () => void;
    const saving = new Promise<void>(resolve => { finish = resolve; });
    let current = { account: "owner-a", workspace: "project-a", session: "session-a" };
    const start = current;
    const ready = waitForReplyLanguageSave(saving, "question grounded in the old account",
      () => assert.fail("successful save must not prefill"), () => start === current);
    current = { ...current, [switchedAt]: "different" };
    finish();
    assert.equal(await ready, false);
  }
});

test("a failed language save cannot prefill an unrelated composer", async () => {
  let reject!: (reason: Error) => void;
  const saving = new Promise<void>((_, fail) => { reject = fail; });
  const restored: string[] = [];
  let remainsOpen = true;
  const ready = waitForReplyLanguageSave(saving, "private draft", draft => restored.push(draft), () => remainsOpen);
  remainsOpen = false;
  reject(new Error("Save failed after leaving the conversation"));
  assert.equal(await ready, false);
  assert.deepEqual(restored, []);
});
