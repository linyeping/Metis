import { metisFileHref, METIS_LINK_KIND_FILE, METIS_LINK_KIND_WEB } from './metisLinks';
import { normalizeLocalPreviewUrl } from './webPreview';

type MdNode = {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
  data?: { hProperties?: Record<string, unknown> };
};

type MetisLinkSegment =
  | { kind: 'text'; value: string }
  | { kind: 'file' | 'web'; value: string; href: string };

const PATH_EXTENSION_RE =
  '(?:markdown|jsonc|jsx|tsx|scss|less|xhtml|html?|yaml|toml|xml|sql|java|bash|zsh|fish|cmd|ps1|cpp|cxx|hpp|svelte|astro|dockerfile|py|js|ts|css|md|json|yml|go|kt|rs|rb|php|sh|bat|c|cc|h|vue|txt|log|csv|ini|cfg|conf|env)';
const FILE_PATH_RE = new RegExp(
  [
    `[A-Za-z]:[\\\\/][^\\s<>"'\`)\\]}]+?\\.${PATH_EXTENSION_RE}`,
    `\\.{1,2}[\\\\/][^\\s<>"'\`)\\]}]+?\\.${PATH_EXTENSION_RE}`,
    `[A-Za-z0-9_.@-]+[\\\\/][^\\s<>"'\`)\\]}]+?\\.${PATH_EXTENSION_RE}`,
    `\\b[A-Za-z0-9_.-]+\\.html?\\b`,
  ].join('|'),
  'gi',
);
const LOCAL_PREVIEW_URL_RE =
  /https?:\/\/(?:localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1\])(?::\d{1,5})?(?:\/[^\s<>"'`)\]}]*)?/gi;
const LOCAL_PREVIEW_HOST_PORT_RE =
  /(?:^|[\s([>])((?:localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1\]):\d{2,5}(?:\/[^\s<>"'`)\]}]*)?)/gi;
const TRAILING_PUNCTUATION_RE = /[.,;:!?]+$/;
// GFM's autolink-literal only trims ASCII trailing punctuation and stops at
// whitespace. When a model writes a bare URL immediately followed by CJK
// prose with no space and full-width punctuation (e.g. "https://x.com）的内容："),
// remark-gfm swallows everything up to the next real whitespace into the
// link. This is the boundary of what remark-gfm could plausibly have meant
// as "the URL": a run of printable ASCII characters.
const URL_SAFE_RUN_RE = /^[!-~]+/;

export function remarkMetisLinks() {
  return (tree: MdNode) => {
    transformNode(tree);
  };
}

export function linkifyMetisText(value: string): MetisLinkSegment[] {
  const text = String(value || '');
  if (!text) return [{ kind: 'text', value: text }];
  const candidates = collectCandidates(text);
  if (candidates.length === 0) return [{ kind: 'text', value: text }];

  const segments: MetisLinkSegment[] = [];
  let cursor = 0;
  for (const candidate of candidates) {
    if (candidate.start < cursor) continue;
    if (candidate.start > cursor) {
      segments.push({ kind: 'text', value: text.slice(cursor, candidate.start) });
    }
    segments.push({ kind: candidate.kind, value: candidate.value, href: candidate.href });
    cursor = candidate.end;
  }
  if (cursor < text.length) {
    segments.push({ kind: 'text', value: text.slice(cursor) });
  }
  return segments.length > 0 ? segments : [{ kind: 'text', value: text }];
}

function transformNode(node: MdNode): void {
  if (node.type === 'code') return;
  if (node.type === 'link') {
    markLinkKind(node);
    return;
  }
  if (!node.children) return;

  for (let index = 0; index < node.children.length; index += 1) {
    const child = node.children[index];
    if (child.type === 'text' || child.type === 'inlineCode') {
      const replacements = linkifyMetisText(child.value || '').map(segmentToNode);
      if (replacements.length !== 1 || replacements[0].type !== child.type || replacements[0].value !== child.value) {
        node.children.splice(index, 1, ...replacements);
        index += replacements.length - 1;
      }
      continue;
    }
    if (child.type === 'link') {
      const replacements = splitOverrunAutolink(child);
      markLinkKind(replacements[0]);
      if (replacements.length > 1) {
        node.children.splice(index, 1, ...replacements);
        index += replacements.length - 1;
        continue;
      }
    }
    transformNode(child);
  }
}

/** Trim a GFM autolink-literal node back to its real URL when remark-gfm
 *  over-matched into trailing CJK/full-width-punctuation text, and return
 *  the recovered trailing text as a sibling node. No-op for normal links
 *  (e.g. `[label](url)`) where the label differs from the URL. */
function splitOverrunAutolink(node: MdNode): MdNode[] {
  const url = String(node.url || '');
  if (!/^https?:\/\//i.test(url)) return [node];
  const children = node.children || [];
  if (children.length !== 1 || children[0].type !== 'text') return [node];
  const text = String(children[0].value || '');
  if (text !== url) return [node];

  const match = text.match(URL_SAFE_RUN_RE);
  const safeRun = match ? match[0] : '';
  const trimmed = safeRun.replace(TRAILING_PUNCTUATION_RE, '');
  if (!trimmed || trimmed.length >= text.length) return [node];

  const remainder = text.slice(trimmed.length);
  node.url = trimmed;
  children[0].value = trimmed;
  return [node, { type: 'text', value: remainder }];
}

function segmentToNode(segment: MetisLinkSegment): MdNode {
  if (segment.kind === 'text') return { type: 'text', value: segment.value };
  return {
    type: 'link',
    url: segment.href,
    data: { hProperties: { 'data-link-kind': segment.kind } },
    children: [{ type: 'text', value: segment.value }],
  };
}

function markLinkKind(node: MdNode): void {
  const url = String(node.url || '');
  const kind = url.toLowerCase().startsWith('metis-file:')
    ? METIS_LINK_KIND_FILE
    : /^https?:\/\//i.test(url)
      ? METIS_LINK_KIND_WEB
      : '';
  if (!kind) return;
  node.data = node.data || {};
  node.data.hProperties = {
    ...(node.data.hProperties || {}),
    'data-link-kind': kind,
  };
}

function collectCandidates(text: string): Array<{ start: number; end: number; kind: 'file' | 'web'; value: string; href: string }> {
  const candidates: Array<{ start: number; end: number; kind: 'file' | 'web'; value: string; href: string }> = [];

  for (const match of text.matchAll(LOCAL_PREVIEW_URL_RE)) {
    addCandidate(candidates, text, match.index || 0, match[0], 'web', normalizeLocalPreviewUrl(match[0]));
  }
  for (const match of text.matchAll(LOCAL_PREVIEW_HOST_PORT_RE)) {
    const hostPort = match[1] || '';
    const offset = match[0].indexOf(hostPort);
    addCandidate(candidates, text, (match.index || 0) + Math.max(0, offset), hostPort, 'web', normalizeLocalPreviewUrl(`http://${hostPort}`));
  }
  for (const match of text.matchAll(FILE_PATH_RE)) {
    const raw = match[0] || '';
    const start = match.index || 0;
    addCandidate(candidates, text, start, raw, 'file', metisFileHref(trimCandidate(raw).value));
  }

  return candidates
    .filter(candidate => Boolean(candidate.href && candidate.value))
    .sort((left, right) => left.start - right.start || right.end - left.end)
    .reduce<typeof candidates>((accepted, candidate) => {
      if (accepted.some(item => rangesOverlap(item.start, item.end, candidate.start, candidate.end))) return accepted;
      accepted.push(candidate);
      return accepted;
    }, []);
}

function addCandidate(
  candidates: Array<{ start: number; end: number; kind: 'file' | 'web'; value: string; href: string }>,
  text: string,
  start: number,
  rawValue: string,
  kind: 'file' | 'web',
  href: string,
): void {
  if (!rawValue || !href) return;
  const trimmed = trimCandidate(rawValue);
  if (!trimmed.value) return;
  candidates.push({
    start,
    end: Math.min(text.length, start + trimmed.value.length),
    kind,
    value: trimmed.value,
    href,
  });
}

function trimCandidate(value: string): { value: string } {
  return { value: String(value || '').replace(TRAILING_PUNCTUATION_RE, '') };
}

function rangesOverlap(leftStart: number, leftEnd: number, rightStart: number, rightEnd: number): boolean {
  return leftStart < rightEnd && rightStart < leftEnd;
}
