import { useEffect, useState, type CSSProperties } from 'react';
import type { PetAnimationState } from '../lib/types';
import { petRows } from './catalog';

type PetSpriteProps = {
  animate?: boolean;
  className?: string;
  spriteUrl: string;
  state?: PetAnimationState;
};

export function PetSprite({ animate = true, className = '', spriteUrl, state = 'idle' }: PetSpriteProps) {
  const definition = petRows[state];
  const [frame, setFrame] = useState(0);
  const [atlasRows, setAtlasRows] = useState(9);

  useEffect(() => {
    let canceled = false;
    const image = new Image();
    image.onload = () => {
      if (canceled || image.naturalWidth <= 0) return;
      const cellWidth = image.naturalWidth / 8;
      const estimatedRows = Math.round(image.naturalHeight / (cellWidth * (208 / 192)));
      setAtlasRows(estimatedRows === 11 ? 11 : 9);
    };
    image.src = spriteUrl;
    return () => {
      canceled = true;
    };
  }, [spriteUrl]);

  useEffect(() => {
    setFrame(0);
    if (!animate || definition.frames <= 1) return undefined;
    const timer = window.setInterval(() => {
      setFrame(current => (current + 1) % definition.frames);
    }, Math.round(1000 / definition.fps));
    return () => window.clearInterval(timer);
  }, [animate, definition.fps, definition.frames, state]);

  const style = {
    '--pet-atlas-image': `url("${spriteUrl}")`,
    '--pet-atlas-size': `800% ${atlasRows * 100}%`,
    '--pet-atlas-x': `${(frame / 7) * 100}%`,
    '--pet-atlas-y': `${(definition.row / Math.max(1, atlasRows - 1)) * 100}%`,
  } as CSSProperties;

  return <span aria-hidden="true" className={`pet-sprite ${className}`.trim()} style={style} />;
}
