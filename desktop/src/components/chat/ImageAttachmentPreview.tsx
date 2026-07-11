import { Download, Minus, Plus, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { useT } from '../../hooks/useT';
import { useUiStore } from '../../store/uiStore';

export const IMAGE_PREVIEW_MIN_ZOOM = 25;
export const IMAGE_PREVIEW_MAX_ZOOM = 300;
export const IMAGE_PREVIEW_ZOOM_STEP = 1;

export function clampImagePreviewZoom(value: number): number {
  if (!Number.isFinite(value)) return 100;
  return Math.min(IMAGE_PREVIEW_MAX_ZOOM, Math.max(IMAGE_PREVIEW_MIN_ZOOM, Math.round(value)));
}

export function parseImagePreviewZoom(value: string, fallback = 100): number {
  const parsed = Number(value.replace('%', '').trim());
  return Number.isFinite(parsed) && value.trim() !== ''
    ? clampImagePreviewZoom(parsed)
    : clampImagePreviewZoom(fallback);
}

export function ImageAttachmentPreview({ contextKey }: { contextKey: string }) {
  const preview = useUiStore(state => state.imageAttachmentPreview);
  const setPreview = useUiStore(state => state.setImageAttachmentPreview);
  const pushToast = useUiStore(state => state.pushToast);
  const t = useT();
  const [zoom, setZoom] = useState(100);
  const [zoomInput, setZoomInput] = useState('100');
  const previousContextKey = useRef(contextKey);

  useEffect(() => {
    setZoom(100);
    setZoomInput('100');
  }, [preview?.src]);

  const applyZoom = useCallback((value: number) => {
    const nextZoom = clampImagePreviewZoom(value);
    setZoom(nextZoom);
    setZoomInput(String(nextZoom));
  }, []);

  useEffect(() => {
    if (previousContextKey.current === contextKey) return;
    previousContextKey.current = contextKey;
    setPreview(null);
  }, [contextKey, setPreview]);

  useEffect(() => {
    if (!preview) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setPreview(null);
        return;
      }
      if (!event.ctrlKey && !event.metaKey) return;
      if (event.key === '+' || event.key === '=') {
        event.preventDefault();
        applyZoom(zoom + IMAGE_PREVIEW_ZOOM_STEP);
      } else if (event.key === '-') {
        event.preventDefault();
        applyZoom(zoom - IMAGE_PREVIEW_ZOOM_STEP);
      } else if (event.key === '0') {
        event.preventDefault();
        applyZoom(100);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [applyZoom, preview, setPreview, zoom]);

  if (!preview) return null;

  const changeZoom = (delta: number) => {
    applyZoom(zoom + delta);
  };

  const commitZoomInput = () => {
    applyZoom(parseImagePreviewZoom(zoomInput, zoom));
  };

  const downloadImage = async () => {
    try {
      if (window.metis?.saveBinaryFile) {
        const result = await window.metis.saveBinaryFile({
          dataUrl: preview.src,
          defaultPath: preview.name,
          filters: [imageDownloadFilter(preview)],
        });
        if (result.error) throw new Error(result.error);
        return;
      }
      const anchor = document.createElement('a');
      anchor.href = preview.src;
      anchor.download = preview.name || 'image.png';
      anchor.click();
    } catch (error) {
      pushToast({
        title: t('图片下载失败'),
        description: error instanceof Error ? error.message : t('无法保存图片'),
        type: 'error',
      });
    }
  };

  const previewStyle = {
    '--image-preview-zoom': `${zoom}%`,
  } as CSSProperties;

  return (
    <section
      className="image-attachment-preview"
      role="dialog"
      aria-modal="true"
      aria-label={t('图片预览')}
      style={previewStyle}
    >
      <div className="image-attachment-preview-toolbar">
        <button type="button" onClick={() => void downloadImage()} title={t('下载图片')}>
          <Download size={15} />
          <span>{t('下载')}</span>
        </button>
        <button type="button" onClick={() => setPreview(null)} title={t('退出预览')}>
          <X size={15} />
          <span>{t('退出预览')}</span>
        </button>
      </div>

      <div className="image-attachment-preview-stage">
        <div className="image-attachment-preview-canvas">
          <img src={preview.src} alt={preview.name} draggable={false} />
        </div>
      </div>

      <div className="image-attachment-preview-zoom" aria-label={t('图片缩放')}>
        <button
          type="button"
          aria-label={t('缩小')}
          title={t('缩小')}
          disabled={zoom <= IMAGE_PREVIEW_MIN_ZOOM}
          onClick={() => changeZoom(-IMAGE_PREVIEW_ZOOM_STEP)}
        >
          <Minus size={16} />
        </button>
        <label className="image-attachment-preview-zoom-value">
          <input
            type="text"
            inputMode="numeric"
            aria-label={t('缩放百分比')}
            value={zoomInput}
            maxLength={3}
            onChange={event => setZoomInput(event.target.value.replace(/\D/g, '').slice(0, 3))}
            onFocus={event => event.currentTarget.select()}
            onBlur={commitZoomInput}
            onKeyDown={event => {
              if (event.key !== 'Enter') return;
              event.preventDefault();
              event.currentTarget.blur();
            }}
          />
          <span aria-hidden="true">%</span>
        </label>
        <button
          type="button"
          aria-label={t('放大')}
          title={t('放大')}
          disabled={zoom >= IMAGE_PREVIEW_MAX_ZOOM}
          onClick={() => changeZoom(IMAGE_PREVIEW_ZOOM_STEP)}
        >
          <Plus size={16} />
        </button>
      </div>
    </section>
  );
}

function imageDownloadFilter(preview: { name: string; mime: string }): { name: string; extensions: string[] } {
  const dotIndex = preview.name.lastIndexOf('.');
  const fromName = dotIndex > -1 ? preview.name.slice(dotIndex + 1).trim().toLowerCase() : '';
  const fromMime = preview.mime.split('/').pop()?.replace('jpeg', 'jpg').replace('svg+xml', 'svg').toLowerCase() || '';
  const extension = /^[a-z0-9]+$/.test(fromName) ? fromName : (/^[a-z0-9]+$/.test(fromMime) ? fromMime : 'png');
  return {
    name: 'Image',
    extensions: [extension],
  };
}
