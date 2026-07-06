import { listArtifacts } from './api';
import type { ArtifactRecord } from './types';

export type DocumentLibraryItemKind =
  | 'research_report'
  | 'markdown'
  | 'file'
  | 'file_change'
  | 'diff'
  | 'report'
  | 'document'
  | 'preview_evidence'
  | 'download'
  | 'workspace_file';

export interface DocumentLibraryItem {
  id: string;
  kind: DocumentLibraryItemKind;
  title: string;
  subtitle?: string;
  path?: string;
  url?: string;
  mime?: string;
  artifactId?: string;
  jobId?: string;
  source?: string;
  metadata?: Record<string, unknown>;
  createdAt: number;
  updatedAt: number;
}

const STORAGE_KEY = 'metis.documentLibrary.v1';
export const DOCUMENT_LIBRARY_EVENT = 'metis-document-library-updated';
const KNOWN_KINDS: DocumentLibraryItemKind[] = [
  'research_report',
  'markdown',
  'file',
  'file_change',
  'diff',
  'report',
  'document',
  'preview_evidence',
  'download',
  'workspace_file',
];

function nowMs(): number {
  return Date.now();
}

function readRawItems(): DocumentLibraryItem[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]') as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.map(coerceItem).filter(Boolean) as DocumentLibraryItem[];
  } catch {
    return [];
  }
}

function coerceItem(value: unknown): DocumentLibraryItem | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  const id = String(row.id || row.jobId || row.path || '').trim();
  const title = String(row.title || row.path || row.jobId || '').trim();
  if (!id || !title) return null;
  return {
    id,
    kind: KNOWN_KINDS.includes(String(row.kind) as DocumentLibraryItemKind) ? (row.kind as DocumentLibraryItemKind) : 'file',
    title,
    subtitle: String(row.subtitle || ''),
    path: String(row.path || ''),
    url: String(row.url || ''),
    mime: String(row.mime || ''),
    artifactId: String(row.artifactId || row.artifact_id || ''),
    jobId: String(row.jobId || ''),
    source: String(row.source || ''),
    metadata: row.metadata && typeof row.metadata === 'object' ? (row.metadata as Record<string, unknown>) : {},
    createdAt: Number(row.createdAt || row.created_at || 0) || nowMs(),
    updatedAt: Number(row.updatedAt || row.updated_at || 0) || nowMs(),
  };
}

function writeItems(items: DocumentLibraryItem[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, 120)));
  window.dispatchEvent(new CustomEvent(DOCUMENT_LIBRARY_EVENT));
}

export function listDocumentLibraryItems(): DocumentLibraryItem[] {
  return readRawItems().sort((left, right) => Number(right.updatedAt || 0) - Number(left.updatedAt || 0));
}

export function upsertDocumentLibraryItem(item: Omit<DocumentLibraryItem, 'createdAt' | 'updatedAt'> & Partial<Pick<DocumentLibraryItem, 'createdAt' | 'updatedAt'>>): DocumentLibraryItem {
  const current = readRawItems();
  const timestamp = nowMs();
  const id = String(item.id || item.jobId || item.path || `doc-${timestamp}`).trim();
  const existing = current.find(row => row.id === id);
  const next: DocumentLibraryItem = {
    ...existing,
    ...item,
    id,
    title: String(item.title || existing?.title || 'Document'),
    createdAt: Number(existing?.createdAt || item.createdAt || timestamp),
    updatedAt: Number(item.updatedAt || timestamp),
  };
  writeItems([next, ...current.filter(row => row.id !== id)]);
  return next;
}

export async function syncDocumentLibraryFromArtifacts(
  sourceOrOptions?: ArtifactRecord[] | { sessionId?: string; runId?: string; kind?: string; limit?: number; includeUnscoped?: boolean },
): Promise<DocumentLibraryItem[]> {
  const options = Array.isArray(sourceOrOptions) ? {} : (sourceOrOptions || {});
  const fetched = Array.isArray(sourceOrOptions)
    ? sourceOrOptions
    : (await listArtifacts({ runId: options.runId, kind: options.kind, limit: options.limit || 120 })).artifacts;
  const source = options.sessionId
    ? fetched.filter(artifact => artifact.session_id === options.sessionId || (options.includeUnscoped !== false && !artifact.session_id))
    : fetched;
  const artifactItems = source.map(documentItemFromArtifact).filter(Boolean) as DocumentLibraryItem[];
  const artifactIds = new Set(artifactItems.map(item => item.id));
  const legacyItems = readRawItems().filter(item => !item.artifactId && !artifactIds.has(item.id));
  const next = [...artifactItems, ...legacyItems]
    .sort((left, right) => Number(right.updatedAt || 0) - Number(left.updatedAt || 0))
    .slice(0, 120);
  writeItems(next);
  return next;
}

export function documentItemFromArtifact(artifact: ArtifactRecord): DocumentLibraryItem | null {
  const artifactId = String(artifact.artifact_id || '').trim();
  if (!artifactId) return null;
  const metadata = artifact.metadata || {};
  const jobId = typeof metadata.job_id === 'string' ? metadata.job_id : '';
  const createdAt = isoToMs(artifact.created_at) || nowMs();
  const updatedAt = isoToMs(artifact.updated_at) || createdAt;
  return {
    id: artifactId,
    artifactId,
    kind: normalizeArtifactKind(artifact.kind),
    title: artifact.title || artifact.path || artifact.url || 'Artifact',
    subtitle: artifactSubtitle(artifact),
    path: artifact.path || '',
    url: artifact.url || '',
    mime: artifact.mime || '',
    jobId,
    source: 'artifact_registry',
    metadata,
    createdAt,
    updatedAt,
  };
}

function normalizeArtifactKind(value: string): DocumentLibraryItemKind {
  const kind = String(value || '').trim() as DocumentLibraryItemKind;
  return KNOWN_KINDS.includes(kind) ? kind : 'workspace_file';
}

function artifactSubtitle(artifact: ArtifactRecord): string {
  const kind = String(artifact.kind || 'workspace_file');
  const pathOrUrl = artifact.path || artifact.url || '';
  const validation = officeValidationLabel(artifact.metadata);
  const base = pathOrUrl ? `${kind} · ${pathOrUrl}` : kind;
  return validation ? `${validation} · ${base}` : base;
}

function isoToMs(value: string): number {
  const parsed = Date.parse(value || '');
  return Number.isFinite(parsed) ? parsed : 0;
}

function officeValidationLabel(metadata: Record<string, unknown>): string {
  const validation = metadata.office_validation;
  if (!validation || typeof validation !== 'object') return '';
  const row = validation as Record<string, unknown>;
  if (row.ok === true) return '已验收';
  const summary = typeof row.summary === 'string' ? row.summary : '';
  return summary ? `验收失败: ${summary}` : '验收失败';
}
