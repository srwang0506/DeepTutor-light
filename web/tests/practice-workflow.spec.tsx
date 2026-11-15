import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { PracticeSession } from "@/components/learning/practice/PracticeSession";
import { PracticeImport } from "@/components/learning/practice/PracticeImport";
import { initI18n } from "@/i18n/init";
import * as api from "@/lib/practice-api";
import { consumePendingPrompt } from "@/lib/pending-prompt";

vi.mock("next/dynamic", () => ({
  default:
    () =>
    ({ content }: { content: string }) => <div>{content}</div>,
}));
vi.mock("@/lib/practice-api", async importOriginal => ({
  ...(await importOriginal<typeof api>()),
  getPracticeQuestion: vi.fn(),
  getPracticeObjectives: vi.fn(),
  setPracticeObjective: vi.fn(),
  checkPracticeAnswer: vi.fn(),
  savePracticeReview: vi.fn(),
  previewPracticeImport: vi.fn(),
  commitPracticeImport: vi.fn(),
  downloadPracticeTemplate: vi.fn(),
}));
initI18n("en");
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getPracticeQuestion).mockResolvedValue({
    entry: {
      id: 8,
      session_id: "lesson",
      session_title: "Lesson",
      turn_id: "",
      question_id: "q1",
      difficulty: "",
      user_answer: "",
      source: "deep_question",
      material_id: "",
      material_title: "",
      section_id: "",
      section_title: "",
      score_trend: "new",
      is_correct: false,
      resolved: false,
      bookmarked: false,
      followup_session_id: "",
      created_at: 100,
      updated_at: 100,
      question: "Which ocean?",
      options: { A: "Pacific", B: "Atlantic" },
      correct_answer: "B",
      explanation: "Reference explanation",
      question_type: "single_choice",
    },
    state: { version: 2, review_count: 1, is_mistake: true, due_at: 100 },
  });
  vi.mocked(api.checkPracticeAnswer).mockResolvedValue({ correct: true });
  vi.mocked(api.savePracticeReview).mockResolvedValue({
    due_at: 200000,
    is_mistake: true,
    correct: true,
    rating: "good",
  });
});

it("hides reference answers, waits for the server, and only counts saved reviews", async () => {
  let finish!: (value: Awaited<ReturnType<typeof api.savePracticeReview>>) => void;
  vi.mocked(api.savePracticeReview).mockImplementation(
    () =>
      new Promise(resolve => {
        finish = resolve;
      }),
  );
  render(<PracticeSession ids={[8]} onClose={vi.fn()} />);
  await screen.findByText("Which ocean?");
  expect(screen.queryByText("Reference explanation")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("radio", { name: "B Atlantic" }));
  fireEvent.click(screen.getByRole("button", { name: "Check and reveal answer" }));
  await screen.findByText("Reference explanation");
  const check = vi.mocked(api.checkPracticeAnswer).mock.calls[0][3];
  expect(api.checkPracticeAnswer).toHaveBeenCalledWith(
    8, "B", undefined, { request_id: expect.any(String), version: 2 },
  );
  fireEvent.click(screen.getByRole("button", { name: "Save recall · Next question" }));
  expect(screen.queryByText("Practice complete")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save recall · Next question" })).toBeDisabled();
  await act(async () =>
    finish({ due_at: 200000, is_mistake: true, correct: true, rating: "good" }),
  );
  expect(await screen.findByText("Practice complete")).toBeInTheDocument();
  expect(api.savePracticeReview).toHaveBeenCalledWith(
    8,
    expect.objectContaining({ request_id: check?.request_id, version: 2, answer: "B", rating: "good" }),
    undefined,
  );
});

it("keeps a failed save retryable with the same submission ID", async () => {
  vi.mocked(api.savePracticeReview).mockRejectedValueOnce(new Error("Network interrupted"));
  render(<PracticeSession ids={[8]} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("radio", { name: "B Atlantic" }));
  fireEvent.click(screen.getByRole("button", { name: "Check and reveal answer" }));
  fireEvent.click(await screen.findByRole("button", { name: "Save recall · Next question" }));
  await screen.findByText("Network interrupted");
  expect(screen.queryByText("Practice complete")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry saving review" }));
  await screen.findByText("Practice complete");
  expect(screen.getByText("1 reviews saved. Your next review dates have been updated.")).toBeVisible();
  expect(vi.mocked(api.savePracticeReview).mock.calls[0]).toEqual(
    vi.mocked(api.savePracticeReview).mock.calls[1],
  );
});

it("keeps an uncertain reschedule submission frozen until the same receipt is retried", async () => {
  vi.mocked(api.savePracticeReview).mockRejectedValueOnce(new Error("Response lost"));
  render(<PracticeSession ids={[8]} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("radio", { name: "A Pacific" }));
  fireEvent.click(screen.getByRole("button", { name: "Skip recall and reschedule" }));
  await screen.findByText("Response lost");

  expect(screen.getByRole("radio", { name: "A Pacific" })).toBeChecked();
  expect(screen.getByRole("radio", { name: "B Atlantic" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Check and reveal answer" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Skip recall and reschedule" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Skip for now" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Link a learning objective" })).toBeDisabled();
  expect(api.checkPracticeAnswer).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Retry saving review" }));
  await screen.findByText("Practice complete");
  const calls = vi.mocked(api.savePracticeReview).mock.calls;
  expect(calls).toHaveLength(2);
  expect(calls[1]).toEqual(calls[0]);
  expect(calls[1][1]).toMatchObject({ answer: "A", self_report: true, rating: "good" });
  expect(screen.getByText("1 reviews saved. Your next review dates have been updated.")).toBeVisible();
});

it("counts every committed recall while keeping repeated questions in the round", async () => {
  vi.mocked(api.checkPracticeAnswer).mockResolvedValueOnce({ correct: false });
  vi.mocked(api.savePracticeReview).mockResolvedValueOnce({
    due_at: 200, is_mistake: true, correct: false, rating: "again",
  });
  render(<PracticeSession ids={[8]} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("radio", { name: "A Pacific" }));
  fireEvent.click(screen.getByRole("button", { name: "Check and reveal answer" }));
  fireEvent.click(await screen.findByRole("button", { name: "Practice again" }));
  await waitFor(() => expect(api.getPracticeQuestion).toHaveBeenCalledTimes(2));
  expect(screen.getByText("1 remaining")).toBeVisible();
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "0");

  fireEvent.click(await screen.findByRole("radio", { name: "B Atlantic" }));
  fireEvent.click(screen.getByRole("button", { name: "Check and reveal answer" }));
  fireEvent.click(await screen.findByRole("button", { name: "Save recall · Next question" }));
  await screen.findByText("Practice complete");
  expect(screen.getByText("2 reviews saved. Your next review dates have been updated.")).toBeVisible();
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "1");
  expect(screen.getByRole("progressbar")).toHaveAttribute("max", "1");
});

it("lets an unavailable question be skipped without pretending it was reviewed", async () => {
  vi.mocked(api.getPracticeQuestion).mockRejectedValueOnce(new Error("Question unavailable"));
  render(<PracticeSession ids={[8, 9]} onClose={vi.fn()} />);
  await screen.findByText("Question unavailable");
  expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reload question" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
  await waitFor(() => expect(api.getPracticeQuestion).toHaveBeenLastCalledWith(9, undefined));
  fireEvent.click(await screen.findByRole("button", { name: "Skip recall and reschedule" }));
  await screen.findByText("Practice complete");
  expect(api.savePracticeReview).toHaveBeenCalledTimes(1);
  expect(api.savePracticeReview).toHaveBeenCalledWith(9, expect.any(Object), undefined);
  expect(screen.getByText("1 skipped questions remain due.")).toBeVisible();
  expect(screen.getByText("1 reviews saved. Your next review dates have been updated.")).toBeVisible();
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "1");
  expect(screen.getByRole("progressbar")).toHaveAttribute("max", "2");
});

it("does not allow a success rating when the server marked the answer wrong", async () => {
  vi.mocked(api.checkPracticeAnswer).mockResolvedValue({ correct: false });
  render(<PracticeSession ids={[8]} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("radio", { name: "A Pacific" }));
  fireEvent.click(screen.getByRole("button", { name: "Check and reveal answer" }));
  await screen.findByText("Needs another try");
  expect(screen.queryByRole("button", { name: "Save recall · Next question" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Practice again" })).not.toBeDisabled();
});

it("requires reloading a stale question instead of retrying its old version", async () => {
  vi.mocked(api.savePracticeReview).mockRejectedValueOnce(
    new api.PracticeRequestError("Question changed", 409),
  );
  render(<PracticeSession ids={[8]} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("radio", { name: "B Atlantic" }));
  fireEvent.click(screen.getByRole("button", { name: "Check and reveal answer" }));
  fireEvent.click(await screen.findByRole("button", { name: "Save recall · Next question" }));
  fireEvent.click(await screen.findByRole("button", { name: "Reload question" }));
  await waitFor(() => expect(api.getPracticeQuestion).toHaveBeenCalledTimes(2));
  expect(screen.queryByText("Reference explanation")).not.toBeInTheDocument();
});

it("keeps forgotten questions in the round and removes only mastered questions", async () => {
  vi.mocked(api.checkPracticeAnswer).mockResolvedValueOnce({ correct: false });
  vi.mocked(api.savePracticeReview).mockResolvedValueOnce({ due_at: 200, is_mistake: true, correct: false, rating: "again" });
  render(<PracticeSession questions={[{ id: 8, content_workspace_id: "source-a" }]} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("radio", { name: "A Pacific" }));
  fireEvent.click(screen.getByRole("button", { name: "Check and reveal answer" }));
  fireEvent.click(await screen.findByRole("button", { name: "Practice again" }));
  await waitFor(() => expect(api.getPracticeQuestion).toHaveBeenCalledTimes(2));
  expect(screen.getByText("1 remaining")).toBeInTheDocument();
  expect(screen.queryByText("Practice complete")).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Skip recall and reschedule" }));
  expect(await screen.findByText("Practice complete")).toBeInTheDocument();
  expect(api.savePracticeReview).toHaveBeenLastCalledWith(8, expect.objectContaining({ self_report: true, rating: "good" }), "source-a");
  expect(screen.getByText("0 remaining")).toBeInTheDocument();
});

it("loads and saves equal question IDs in their own workspaces", async () => {
  render(<PracticeSession questions={[{ id: 8, content_workspace_id: "" }, { id: 8, content_workspace_id: "other" }]} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Skip recall and reschedule" }));
  await waitFor(() => expect(api.getPracticeQuestion).toHaveBeenLastCalledWith(8, "other"));
  fireEvent.click(await screen.findByRole("button", { name: "Skip recall and reschedule" }));
  await screen.findByText("Practice complete");
  expect(vi.mocked(api.savePracticeReview).mock.calls.map(call => call[2])).toEqual(["", "other"]);
});

it("previews import errors without committing any rows", async () => {
  vi.mocked(api.previewPracticeImport).mockResolvedValue({
    token: null,
    total: 2,
    valid: 1,
    errors: [{ row: 3, message: "Answer is required" }],
    samples: [],
  });
  render(<PracticeImport onClose={vi.fn()} onImported={vi.fn()} initialTarget="bank" />);
  fireEvent.change(screen.getByLabelText(/Choose a question file/), {
    target: { files: [new File(["q,a"], "questions.csv")] },
  });
  expect(await screen.findByText(/Answer is required/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm import" })).toBeDisabled();
  expect(api.commitPracticeImport).not.toHaveBeenCalled();
});

it("keeps a successful preview available when import saving fails", async () => {
  vi.mocked(api.previewPracticeImport).mockResolvedValue({
    token: "a".repeat(32),
    total: 1,
    valid: 1,
    errors: [],
    samples: [],
  });
  vi.mocked(api.commitPracticeImport)
    .mockRejectedValueOnce(new Error("Save unavailable"))
    .mockResolvedValueOnce({ created: 1, duplicates: 0 });
  const onImported = vi.fn();
  render(<PracticeImport onClose={vi.fn()} onImported={onImported} initialTarget="mistakes" />);
  fireEvent.change(screen.getByLabelText(/Choose a question file/), {
    target: { files: [new File(["q,a"], "questions.csv")] },
  });
  fireEvent.click(await screen.findByRole("button", { name: "Confirm import" }));
  await screen.findByText("Save unavailable");
  expect(onImported).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm import" }));
  await waitFor(() => expect(onImported).toHaveBeenCalledOnce());
  expect(api.previewPracticeImport).toHaveBeenCalledWith(expect.any(File), "mistakes");
});


it("formats the next review date in the UI language rather than the browser locale", async () => {
  const formatDate = Date.prototype.toLocaleDateString;
  const dateSpy = vi
    .spyOn(Date.prototype, "toLocaleDateString")
    .mockImplementation(function (this: Date, locales, options) {
      return formatDate.call(this, locales ?? "zh-CN", options);
    });
  try {
    render(<PracticeSession ids={[8]} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Skip recall and reschedule" }));
    await screen.findByText("Practice complete");
    const expectedDate = formatDate.call(new Date(200000 * 1000), "en-US");
    expect(screen.getByText(`Next review: ${expectedDate}`)).toBeVisible();
    expect(dateSpy).toHaveBeenCalledWith("en-US");
  } finally {
    dateSpy.mockRestore();
  }
});

it("binds to an explicitly selected objective and checks the new version", async () => {
  vi.mocked(api.getPracticeObjectives).mockResolvedValue({
    paths: [{ path_id: "dsa", name: "Data structures", objectives: [{ id: "bfs", name: "BFS queues" }] }],
  });
  vi.mocked(api.setPracticeObjective).mockResolvedValue({
    state: { version: 3, review_count: 1, is_mistake: true, due_at: 100, objective: { path_id: "dsa", knowledge_point_id: "bfs" } },
  });
  render(<PracticeSession questions={[{ id: 8, content_workspace_id: "source-a" }]} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Link a learning objective" }));
  fireEvent.change(await screen.findByLabelText("Learning path"), { target: { value: "dsa" } });
  fireEvent.change(screen.getByLabelText("Learning objective"), { target: { value: "bfs" } });
  expect(api.setPracticeObjective).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm objective link" }));
  await screen.findByRole("button", { name: "Change linked objective" });
  expect(api.setPracticeObjective).toHaveBeenCalledWith(8, 2, { path_id: "dsa", knowledge_point_id: "bfs" }, "source-a");
  fireEvent.click(screen.getByRole("radio", { name: "B Atlantic" }));
  fireEvent.click(screen.getByRole("button", { name: "Check and reveal answer" }));
  await screen.findByText("Reference explanation");
  expect(api.checkPracticeAnswer).toHaveBeenCalledWith(8, "B", "source-a", {
    request_id: expect.any(String),
    version: 3,
  });
});

it("prepares a scoped editable follow-up from this saved answer and its receipt", async () => {
  vi.mocked(api.savePracticeReview).mockResolvedValue({
    due_at: 200000, is_mistake: false, correct: true, rating: "good",
    attempt_id: "receipt_1", objective_evidence: "applied", independent: true,
  });
  render(<PracticeSession questions={[{ id: 8, content_workspace_id: "source-a" }]} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("radio", { name: "B Atlantic" }));
  fireEvent.click(screen.getByRole("button", { name: "Check and reveal answer" }));
  fireEvent.click(await screen.findByRole("button", { name: "Save recall · Next question" }));
  const link = await screen.findByRole("link", { name: "Ask your tutor about this attempt" });
  expect(link).toHaveAttribute("href", "/chat?dt_workspace=source-a");
  expect(screen.getByText("Recall evidence added to your learning objective")).toBeVisible();
  expect(consumePendingPrompt("chat:source-a")).toBe("");
  fireEvent.click(link);
  const draft = consumePendingPrompt("chat:source-a");
  expect(draft).toContain("My answer in this attempt: B");
  expect(draft).toContain("Practice attempt ID: receipt_1");
  expect(draft).toContain("/chat/lesson?dt_workspace=source-a");
  expect(consumePendingPrompt()).toBe("");
});
