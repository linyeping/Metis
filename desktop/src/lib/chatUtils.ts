import type { ParsedFile } from './types';

export function contentToText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (content === null || content === undefined) return '';
  /* Handle multimodal content blocks (OpenAI-style array) */
  if (Array.isArray(content)) {
    const textParts: string[] = [];
    for (const block of content) {
      if (typeof block === 'string') {
        textParts.push(block);
      } else if (block && typeof block === 'object') {
        const b = block as Record<string, unknown>;
        if (b.type === 'text' && typeof b.text === 'string') {
          textParts.push(b.text);
        } else if (b.type === 'image_url') {
          textParts.push('[图片附件]');
        } else if (typeof b.text === 'string') {
          textParts.push(b.text);
        } else if (typeof b.content === 'string') {
          textParts.push(b.content);
        }
      }
    }
    if (textParts.length > 0) return textParts.join('\n');
  }
  try {
    return JSON.stringify(content, null, 2);
  } catch {
    return String(content);
  }
}

export function buildUserContent(text: string, attachments: ParsedFile[]): string | Array<Record<string, unknown>> {
  if (attachments.length === 0) return text;

  /* If any attachment is an image with a data URL, build multimodal content blocks */
  const imageAttachments = attachments.filter(a => a.kind === 'image' && a.dataUrl);
  if (imageAttachments.length > 0) {
    const blocks: Array<Record<string, unknown>> = [];
    if (text) {
      blocks.push({ type: 'text', text });
    }
    for (const attachment of attachments) {
      if (attachment.kind === 'image' && attachment.dataUrl) {
        blocks.push({ type: 'image_url', image_url: { url: attachment.dataUrl } });
      } else {
        blocks.push({ type: 'text', text: `[Attachment: ${attachment.name}]\n${attachment.text || '(No extractable text)'}` });
      }
    }
    if (blocks.length === 0) blocks.push({ type: 'text', text: 'Please use the attached files.' });
    return blocks;
  }

  /* Text-only attachments: concatenate as before */
  const parts = [text || 'Please use the attached files.'];
  for (const attachment of attachments) {
    parts.push(`\n\n[Attachment: ${attachment.name}]\n${attachment.text || '(No extractable text)'}`);
  }
  return parts.join('');
}

export function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '0 B';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function compactPythonPath(fullPath: string): string {
  const parts = fullPath.split(/[\\/]/).filter(Boolean);
  if (parts.length <= 2) return fullPath;
  return parts.slice(-2).join('/');
}
