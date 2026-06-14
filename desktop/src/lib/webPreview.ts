const LOCAL_PREVIEW_URL_RE =
  /https?:\/\/(?:localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1\])(?::\d{1,5})?(?:\/[^\s<>"'`)\]}]*)?/gi;
const LOCAL_PREVIEW_HOST_PORT_RE =
  /(?:^|[\s([>])((?:localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1\]):\d{2,5}(?:\/[^\s<>"'`)\]}]*)?)/gi;
const ANSI_RE = /\x1b\[[0-9;]*m/g;
const PREVIEWABLE_WEB_FILE_RE = /\.(?:html?|xhtml)(?:[?#].*)?$/i;

export function findSafeLocalPreviewUrl(text: string): string {
  const source = String(text || '').replace(ANSI_RE, '');
  for (const match of source.matchAll(LOCAL_PREVIEW_URL_RE)) {
    const url = normalizeLocalPreviewUrl(match[0]);
    if (url) return url;
  }
  for (const match of source.matchAll(LOCAL_PREVIEW_HOST_PORT_RE)) {
    const hostPort = match[1];
    const url = normalizeLocalPreviewUrl(`http://${hostPort}`);
    if (url) return url;
  }
  return '';
}

export function isSafeLocalPreviewUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false;
    const host = parsed.hostname.toLowerCase();
    return host === 'localhost' || host === '127.0.0.1' || host === '0.0.0.0' || host === '[::1]' || host === '::1';
  } catch {
    return false;
  }
}

export function isPreviewableWebFilePath(value: string): boolean {
  const filePath = sanitizeFilePath(value);
  if (!filePath || /^https?:\/\//i.test(filePath)) return false;
  return PREVIEWABLE_WEB_FILE_RE.test(filePath);
}

export function localFilePreviewUrl(apiBaseUrl: string, filePath: string): string {
  const normalizedPath = sanitizeFilePath(filePath);
  if (!isPreviewableWebFilePath(normalizedPath)) return '';
  return `${apiBaseUrl.replace(/\/+$/, '')}/file-preview?path=${encodeURIComponent(normalizedPath)}`;
}

export function normalizeLocalPreviewUrl(value: string): string {
  const url = sanitizeUrl(value);
  if (!isSafeLocalPreviewUrl(url)) return '';
  try {
    const parsed = new URL(url);
    const suffix = url.replace(`${parsed.protocol}//${parsed.host}`, '');
    if (parsed.hostname === '0.0.0.0') {
      parsed.hostname = '127.0.0.1';
    }
    return `${parsed.protocol}//${parsed.host}${suffix}`;
  } catch {
    return '';
  }
}

function sanitizeUrl(value: string): string {
  return value.replace(/[.,;:!?]+$/g, '');
}

function sanitizeFilePath(value: string): string {
  return String(value || '')
    .trim()
    .replace(/^["'`]+|["'`]+$/g, '');
}
