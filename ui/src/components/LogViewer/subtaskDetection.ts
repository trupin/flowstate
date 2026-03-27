/** Shared subtask API detection utilities used by LogViewer and ToolCallBlock. */

export const SUBTASK_URL_PATTERN = /\/subtasks(?:\/|$)/;

export function getInputCommand(input: Record<string, unknown>): string | null {
  if (typeof input.command === 'string') return input.command;
  if (typeof input.description === 'string') return input.description;
  return null;
}
