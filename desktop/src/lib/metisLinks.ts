export const METIS_FILE_PROTOCOL = 'metis-file:';
export const METIS_LINK_KIND_FILE = 'file';
export const METIS_LINK_KIND_WEB = 'web';

export type MetisChatLinkAction =
  | { kind: 'web'; url: string }
  | { kind: 'file'; path: string }
  | null;

export function metisFileHref(path: string): string {
  return `${METIS_FILE_PROTOCOL}${encodeURIComponent(String(path || '').trim())}`;
}

export function decodeMetisFileHref(href: string): string {
  const raw = String(href || '').trim();
  if (!raw.toLowerCase().startsWith(METIS_FILE_PROTOCOL)) return '';
  const encoded = raw.slice(METIS_FILE_PROTOCOL.length);
  try {
    return decodeURIComponent(encoded);
  } catch {
    return encoded;
  }
}

export function chatLinkActionFromHref(href: string, linkKind = ''): MetisChatLinkAction {
  const value = String(href || '').trim();
  const kind = String(linkKind || '').trim().toLowerCase();
  if (!value) return null;
  if (kind === METIS_LINK_KIND_FILE || value.toLowerCase().startsWith(METIS_FILE_PROTOCOL)) {
    const path = decodeMetisFileHref(value) || value;
    return path ? { kind: 'file', path } : null;
  }
  if (kind === METIS_LINK_KIND_WEB || /^https?:\/\//i.test(value)) {
    return { kind: 'web', url: value };
  }
  return null;
}
