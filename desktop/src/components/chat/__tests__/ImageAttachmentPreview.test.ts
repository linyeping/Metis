import { describe, expect, it } from 'vitest';
import {
  clampImagePreviewZoom,
  IMAGE_PREVIEW_MAX_ZOOM,
  IMAGE_PREVIEW_MIN_ZOOM,
  IMAGE_PREVIEW_ZOOM_STEP,
  parseImagePreviewZoom,
} from '../ImageAttachmentPreview';

describe('image attachment preview zoom', () => {
  it('clamps zoom to the supported range', () => {
    expect(clampImagePreviewZoom(0)).toBe(IMAGE_PREVIEW_MIN_ZOOM);
    expect(clampImagePreviewZoom(125)).toBe(125);
    expect(clampImagePreviewZoom(999)).toBe(IMAGE_PREVIEW_MAX_ZOOM);
  });

  it('falls back to 100 percent for invalid values', () => {
    expect(clampImagePreviewZoom(Number.NaN)).toBe(100);
    expect(clampImagePreviewZoom(Number.POSITIVE_INFINITY)).toBe(100);
  });

  it('uses one-percent increments', () => {
    expect(IMAGE_PREVIEW_ZOOM_STEP).toBe(1);
  });

  it('parses direct percentage input and clamps it', () => {
    expect(parseImagePreviewZoom('126')).toBe(126);
    expect(parseImagePreviewZoom('301')).toBe(IMAGE_PREVIEW_MAX_ZOOM);
    expect(parseImagePreviewZoom('', 88)).toBe(88);
  });
});
