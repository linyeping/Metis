interface ActiveRunController {
  assistantId: string;
  controller: AbortController;
  runId: string;
}

const activeRunControllers = new Map<string, ActiveRunController>();
export const processedRunSeq = new Map<string, number>();

export function getActiveRunController(sessionId: string | null): ActiveRunController | null {
  return sessionId ? activeRunControllers.get(sessionId) || null : null;
}

export function hasActiveRunController(sessionId: string | null): boolean {
  return Boolean(sessionId && activeRunControllers.has(sessionId));
}

export function setActiveRunController(sessionId: string, run: ActiveRunController): void {
  activeRunControllers.set(sessionId, run);
}

export function clearActiveRunController(sessionId: string | null, assistantId?: string): void {
  if (!sessionId) return;
  const activeRun = activeRunControllers.get(sessionId);
  if (!activeRun) return;
  if (assistantId && activeRun.assistantId !== assistantId) return;
  activeRunControllers.delete(sessionId);
}
