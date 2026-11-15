import { act, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import type { StreamEvent } from "@/features/chat/model/protocol";
import { TraceFlow } from "@/features/chat/trace/TracePresentation";
import { ensureLanguage, initI18n } from "@/i18n/init";

it("updates the native Studio source tool title when the interface language changes", async () => {
  const i18n = initI18n("en");
  await ensureLanguage("zh");
  const events: StreamEvent[] = [
    {
      type: "tool_call",
      source: "chat",
      stage: "responding",
      content: "read_studio_source",
      timestamp: 1,
      metadata: {
        call_id: "round-1",
        call_kind: "tool_planning",
        trace_kind: "tool_call",
        tool_name: "read_studio_source",
        args: { source_id: "studio-source-id", page: 4 },
      },
    },
  ];

  render(<TraceFlow events={events} isStreaming />);
  expect(screen.getByText("Reading source")).toBeVisible();

  await act(async () => {
    await i18n.changeLanguage("zh");
  });
  expect(screen.getByText("读取资料")).toBeVisible();
  expect(screen.queryByText("Read Studio Source")).not.toBeInTheDocument();

  await act(async () => {
    await i18n.changeLanguage("en");
  });
  expect(screen.getByText("Reading source")).toBeVisible();
});
