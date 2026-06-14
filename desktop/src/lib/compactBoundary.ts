export const COMPACT_BOUNDARY_MARKER = '[Metis Compact Boundary]';

export function compactBoundaryContent(summary: string): string {
  return `${COMPACT_BOUNDARY_MARKER}\n${summary || ''}`;
}

export function isCompactBoundary(text: string): boolean {
  return text.trimStart().startsWith(COMPACT_BOUNDARY_MARKER);
}

export function parseCompactBoundary(text: string): { summary: string } {
  const trimmed = text.trimStart();
  const summary = trimmed.startsWith(COMPACT_BOUNDARY_MARKER)
    ? trimmed.slice(COMPACT_BOUNDARY_MARKER.length).trim()
    : trimmed.trim();
  return { summary };
}
