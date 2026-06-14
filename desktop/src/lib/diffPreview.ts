export type DiffLineKind = 'context' | 'add' | 'remove';

export interface DiffLine {
  kind: DiffLineKind;
  oldLine: number | null;
  newLine: number | null;
  text: string;
}

export type FileChangeKind = 'create' | 'modify' | 'delete' | 'unknown';

export interface FileChangePreview {
  id: string;
  title: string;
  toolName: string;
  path: string;
  kind: FileChangeKind;
  summary: string;
  before: string;
  after: string;
  diffLines: DiffLine[];
}

export interface FileChangeFileSummary {
  path: string;
  title: string;
  kind: FileChangeKind;
  additions: number;
  removals: number;
  preview: FileChangePreview;
}

export interface FileChangeSummary {
  id: string;
  changes: FileChangePreview[];
  files: FileChangeFileSummary[];
  fileCount: number;
  additions: number;
  removals: number;
}

const MUTATION_TOOLS = new Set([
  'write_file',
  'edit_file',
  'replace_file',
  'delete_file',
  'remove_file',
  'create_file',
  'append_file',
  'patch_file',
]);

export function buildFileChangePreview(toolName: string, args: unknown, result: unknown): FileChangePreview | null {
  const normalizedTool = normalizeToolName(toolName);
  if (!isFileChangingTool(normalizedTool)) return null;

  const argRecord = objectValue(args);
  const resultRecord = objectValue(parseMaybeJson(result));
  const path = firstString(
    argRecord.path,
    argRecord.file_path,
    argRecord.filePath,
    argRecord.target_path,
    argRecord.targetPath,
    resultRecord.path,
    resultRecord.file_path,
    resultRecord.filePath,
  ).trim();
  const before = firstString(
    argRecord.before,
    argRecord.old,
    argRecord.old_content,
    argRecord.oldContent,
    argRecord.previous_content,
    argRecord.previousContent,
    argRecord.original,
    argRecord.old_text,
    argRecord.oldText,
    resultRecord.before,
    resultRecord.old_content,
    resultRecord.previous_content,
  );
  const after = firstString(
    argRecord.after,
    argRecord.content,
    argRecord.new_content,
    argRecord.newContent,
    argRecord.text,
    argRecord.new_text,
    argRecord.newText,
    argRecord.replacement,
    resultRecord.after,
    resultRecord.content,
    resultRecord.new_content,
  );
  const kind = changeKind(normalizedTool, before, after);
  const diffLines = makeDiffLines(before, after, kind);
  const displayPath = path || firstPathFromText(result);
  if (!displayPath) return null;

  return {
    id: `${normalizedTool}:${displayPath}:${hashText(`${before}\n---\n${after}`)}`,
    title: fileName(displayPath),
    toolName: normalizedTool,
    path: displayPath,
    kind,
    summary: changeSummary(kind, displayPath, diffLines),
    before,
    after,
    diffLines,
  };
}

export function isFileChangingTool(toolName: string): boolean {
  const normalized = normalizeToolName(toolName);
  return MUTATION_TOOLS.has(normalized) || normalized.includes('write_file') || normalized.includes('edit_file') || normalized.includes('delete_file');
}

export function countDiffLines(preview: FileChangePreview): { additions: number; removals: number } {
  return {
    additions: preview.diffLines.filter(line => line.kind === 'add').length,
    removals: preview.diffLines.filter(line => line.kind === 'remove').length,
  };
}

export function summarizeFileChanges(changes: FileChangePreview[], idSeed = 'file-change-summary'): FileChangeSummary | null {
  const completed = changes.filter(Boolean);
  if (completed.length === 0) return null;

  const byPath = new Map<string, FileChangeFileSummary>();
  let additions = 0;
  let removals = 0;

  for (const change of completed) {
    const counts = countDiffLines(change);
    additions += counts.additions;
    removals += counts.removals;

    const key = change.path || change.title || change.id;
    const existing = byPath.get(key);
    if (existing) {
      byPath.set(key, {
        ...existing,
        additions: existing.additions + counts.additions,
        removals: existing.removals + counts.removals,
        kind: mergeKind(existing.kind, change.kind),
        preview: change,
      });
      continue;
    }

    byPath.set(key, {
      path: change.path,
      title: change.title,
      kind: change.kind,
      additions: counts.additions,
      removals: counts.removals,
      preview: change,
    });
  }

  const files = Array.from(byPath.values());
  return {
    id: `${idSeed}:${hashText(completed.map(change => change.id).join('|'))}`,
    additions,
    changes: completed,
    fileCount: files.length,
    files,
    removals,
  };
}

function normalizeToolName(value: string): string {
  return String(value || '').trim().toLowerCase();
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function parseMaybeJson(value: unknown): unknown {
  if (typeof value !== 'string') return value;
  const text = value.trim();
  if (!text.startsWith('{') && !text.startsWith('[')) return value;
  try {
    return JSON.parse(text);
  } catch {
    return value;
  }
}

function firstString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === 'string') return value;
  }
  return '';
}

function firstPathFromText(value: unknown): string {
  const text = typeof value === 'string' ? value : '';
  const match = text.match(/[A-Za-z]:[\\/][^\n\r"'<>|]+|(?:\.{0,2}[\\/])?[\w.-]+(?:[\\/][\w .-]+)+/);
  return match?.[0]?.trim() || '';
}

function fileName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || path || '文件变更';
}

function changeKind(toolName: string, before: string, after: string): FileChangeKind {
  if (toolName.includes('delete') || toolName.includes('remove')) return 'delete';
  if (toolName.includes('create') || (after && !before)) return 'create';
  if (before || after) return 'modify';
  return 'unknown';
}

function mergeKind(left: FileChangeKind, right: FileChangeKind): FileChangeKind {
  if (left === right) return left;
  if (left === 'unknown') return right;
  if (right === 'unknown') return left;
  return 'modify';
}

function makeDiffLines(before: string, after: string, kind: FileChangeKind): DiffLine[] {
  if (kind === 'delete') {
    const lines = splitLines(before || '(file deleted)');
    return lines.map((text, index) => ({ kind: 'remove', oldLine: index + 1, newLine: null, text }));
  }
  if (kind === 'create') {
    const lines = splitLines(after || '(new file)');
    return lines.map((text, index) => ({ kind: 'add', oldLine: null, newLine: index + 1, text }));
  }
  if (!before && !after) {
    return [{ kind: 'context', oldLine: null, newLine: null, text: '没有可解析的行级内容。请查看工具原始输出。' }];
  }

  const beforeLines = splitLines(before);
  const afterLines = splitLines(after);
  const max = Math.max(beforeLines.length, afterLines.length);
  const rows: DiffLine[] = [];
  let oldLine = 1;
  let newLine = 1;
  for (let index = 0; index < max; index += 1) {
    const left = beforeLines[index];
    const right = afterLines[index];
    if (left === right) {
      rows.push({ kind: 'context', oldLine: left === undefined ? null : oldLine, newLine: right === undefined ? null : newLine, text: left ?? right ?? '' });
      if (left !== undefined) oldLine += 1;
      if (right !== undefined) newLine += 1;
      continue;
    }
    if (left !== undefined) {
      rows.push({ kind: 'remove', oldLine, newLine: null, text: left });
      oldLine += 1;
    }
    if (right !== undefined) {
      rows.push({ kind: 'add', oldLine: null, newLine, text: right });
      newLine += 1;
    }
  }
  return rows;
}

function splitLines(value: string): string[] {
  if (!value) return [];
  return value.replace(/\r\n/g, '\n').split('\n');
}

function changeSummary(kind: FileChangeKind, path: string, lines: DiffLine[]): string {
  const adds = lines.filter(line => line.kind === 'add').length;
  const removes = lines.filter(line => line.kind === 'remove').length;
  const label = kind === 'create' ? '新增' : kind === 'delete' ? '删除' : kind === 'modify' ? '修改' : '变更';
  return `${label} ${path || '文件'} · +${adds} / -${removes}`;
}

function hashText(value: string): string {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }
  return hash.toString(16);
}
