import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { PetAnimationState } from '../lib/types';
import { petRows } from './catalog';

type PetSpriteProps = {
  animate?: boolean;
  className?: string;
  spriteUrl: string;
  state?: PetAnimationState;
  speedMultiplier?: number;
  onMaskChange?: (rectangles: Array<{ x: number; y: number; width: number; height: number }>) => void;
};

export function PetSprite({ animate = true, className = '', onMaskChange, speedMultiplier = 0.7, spriteUrl, state = 'idle' }: PetSpriteProps) {
  const definition = petRows[state];
  const [frame, setFrame] = useState(0);
  const [atlasRows, setAtlasRows] = useState(9);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const spriteRef = useRef<HTMLSpanElement | null>(null);
  const [imageRevision, setImageRevision] = useState(0);

  useEffect(() => {
    let canceled = false;
    const image = new Image();
    image.crossOrigin = 'anonymous';
    image.onload = () => {
      if (canceled || image.naturalWidth <= 0) return;
      imageRef.current = image;
      const cellWidth = image.naturalWidth / 8;
      const estimatedRows = Math.round(image.naturalHeight / (cellWidth * (208 / 192)));
      setAtlasRows(estimatedRows === 11 ? 11 : 9);
      setImageRevision(revision => revision + 1);
    };
    image.src = spriteUrl;
    return () => {
      canceled = true;
      imageRef.current = null;
    };
  }, [spriteUrl]);

  useEffect(() => {
    setFrame(0);
    if (!animate || definition.frames <= 1) return undefined;
    const timer = window.setInterval(() => {
      setFrame(current => (current + 1) % definition.frames);
    }, Math.round(1000 / (definition.fps * Math.max(0.25, speedMultiplier))));
    return () => window.clearInterval(timer);
  }, [animate, definition.fps, definition.frames, speedMultiplier, state]);

  useEffect(() => {
    if (!onMaskChange) return undefined;
    const image = imageRef.current;
    const element = spriteRef.current;
    if (!image || !element || image.naturalWidth <= 0 || image.naturalHeight <= 0) return undefined;
    const updateMask = () => {
      const bounds = element.getBoundingClientRect();
      if (bounds.width <= 0 || bounds.height <= 0) return;
      const cellWidth = Math.round(image.naturalWidth / 8);
      const cellHeight = Math.round(image.naturalHeight / atlasRows);
      const canvas = document.createElement('canvas');
      canvas.width = cellWidth;
      canvas.height = cellHeight;
      const context = canvas.getContext('2d', { willReadFrequently: true });
      if (!context) return;
      context.drawImage(image, frame * cellWidth, definition.row * cellHeight, cellWidth, cellHeight, 0, 0, cellWidth, cellHeight);
      let pixels: Uint8ClampedArray;
      try {
        pixels = context.getImageData(0, 0, cellWidth, cellHeight).data;
      } catch {
        return;
      }
      const block = 4;
      const rows: Array<{ x: number; y: number; width: number; height: number }> = [];
      for (let targetY = 0; targetY < bounds.height; targetY += block) {
        let runStart = -1;
        for (let targetX = 0; targetX < bounds.width; targetX += block) {
          const sourceX0 = Math.floor((targetX / bounds.width) * cellWidth);
          const sourceX1 = Math.min(cellWidth, Math.ceil(((targetX + block) / bounds.width) * cellWidth));
          const sourceY0 = Math.floor((targetY / bounds.height) * cellHeight);
          const sourceY1 = Math.min(cellHeight, Math.ceil(((targetY + block) / bounds.height) * cellHeight));
          let opaque = false;
          for (let sourceY = sourceY0; sourceY < sourceY1 && !opaque; sourceY += 1) {
            for (let sourceX = sourceX0; sourceX < sourceX1; sourceX += 1) {
              if (pixels[(sourceY * cellWidth + sourceX) * 4 + 3] > 8) {
                opaque = true;
                break;
              }
            }
          }
          if (opaque && runStart < 0) runStart = targetX;
          const rowEnded = runStart >= 0 && (!opaque || targetX + block >= bounds.width);
          if (rowEnded) {
            const runEnd = opaque ? Math.min(bounds.width, targetX + block) : targetX;
            rows.push({
              x: Math.max(0, Math.floor(bounds.x + runStart - 1)),
              y: Math.max(0, Math.floor(bounds.y + targetY - 1)),
              width: Math.ceil(runEnd - runStart + 2),
              height: Math.ceil(Math.min(block, bounds.height - targetY) + 2),
            });
            runStart = -1;
          }
        }
      }
      const merged: typeof rows = [];
      for (const rectangle of rows) {
        const previous = merged.at(-1);
        if (previous && previous.x === rectangle.x && previous.width === rectangle.width && previous.y + previous.height >= rectangle.y) {
          previous.height = Math.max(previous.height, rectangle.y + rectangle.height - previous.y);
        } else {
          merged.push(rectangle);
        }
      }
      if (merged.length > 0) onMaskChange(merged);
    };
    updateMask();
    const observer = new ResizeObserver(updateMask);
    observer.observe(element);
    return () => observer.disconnect();
  }, [atlasRows, definition.row, frame, imageRevision, onMaskChange]);

  const style = {
    '--pet-atlas-image': `url("${spriteUrl}")`,
    '--pet-atlas-size': `800% ${atlasRows * 100}%`,
    '--pet-atlas-x': `${(frame / 7) * 100}%`,
    '--pet-atlas-y': `${(definition.row / Math.max(1, atlasRows - 1)) * 100}%`,
  } as CSSProperties;

  return <span ref={spriteRef} aria-hidden="true" className={`pet-sprite ${className}`.trim()} style={style} />;
}
