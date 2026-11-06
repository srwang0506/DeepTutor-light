type Translate = (key: string) => string

// These errors come from the workspace API. Keep their actionable meaning while
// rendering UI copy in the current language, including after a language switch.
const knownMessages = new Set([
  'A workspace name must contain 1 to 100 characters.',
  'Project description must be at most 600 characters.',
  'Invalid project color.',
  'Invalid project memory selection.',
  'A selected memory source is unavailable or is this project.',
  'Project is unavailable.',
  'Workspace not found.',
  'Workspace not found or archived.',
  'The default workspace cannot be renamed or archived.',
  'Restore this workspace before changing its data.',
  'The workspace registry could not be read.',
  'The workspace is no longer registered for this user.',
  'The workspace belongs to a different account context.',
  'Workspace storage moved. Reload before continuing.',
  'Search is limited to 2000 characters.',
])

export function projectErrorMessage(message: string, t: Translate): string {
  if (knownMessages.has(message)) return t(message)
  if (/^(?:Failed to fetch|fetch failed|Load failed|NetworkError)/i.test(message))
    return t('Could not connect. Check your connection and try again.')
  if (/^Request failed \(401\)$/.test(message) || message === 'Not authenticated')
    return t('Your session has expired. Sign in again to continue.')
  if (/^Request failed \(403\)$/.test(message))
    return t('You do not have permission to change this project.')
  if (/^Request failed \(429\)$/.test(message))
    return t('Too many requests. Wait a moment and try again.')
  return t('Could not complete this project action. Reload and try again.')
}
