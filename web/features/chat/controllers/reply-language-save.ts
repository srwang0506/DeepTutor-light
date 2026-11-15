/** Keep a composer draft if its session language could not be saved. */
export async function waitForReplyLanguageSave(
  pending: Promise<void> | null,
  content: string,
  restoreDraft: (content: string) => void,
  isCurrent: () => boolean = () => true,
): Promise<boolean> {
  if (!isCurrent()) return false;
  if (!pending) return true;
  try {
    await pending;
    return isCurrent();
  } catch {
    if (isCurrent()) restoreDraft(content);
    return false;
  }
}
