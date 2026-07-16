import tuxSprite from '../../../open-design/assets/community-pets/tux/spritesheet.webp?url';
import dentistSprite from '../../../open-design/assets/community-pets/dentist/spritesheet.webp?url';
import nyakoSprite from '../../../open-design/assets/community-pets/nyako-shigure/spritesheet.webp?url';
import yorhaSprite from '../../../open-design/assets/community-pets/yorha-sit-2b/spritesheet.webp?url';
import type { CustomPet, Language, PetAnimationState, PetId } from '../lib/types';

export type PetCatalogEntry = {
  id: PetId;
  name: string;
  description: Record<Language, string>;
  spriteUrl: string;
  custom?: boolean;
};

export const petCatalog: PetCatalogEntry[] = [
  {
    id: 'tux',
    name: 'Tux',
    description: { zh: '安静、清晰的像素 Linux 伙伴。', en: 'A calm pixel Linux companion.' },
    spriteUrl: tuxSprite,
  },
  {
    id: 'dentist',
    name: 'Dentist',
    description: { zh: '友好的白衣吉祥物，动作辨识度高。', en: 'A friendly mascot with clear expressive motion.' },
    spriteUrl: dentistSprite,
  },
  {
    id: 'nyako-shigure',
    name: 'Nyako Shigure',
    description: { zh: '沉稳的机械伙伴，适合长时间工作。', en: 'A composed mechanical companion for long sessions.' },
    spriteUrl: nyakoSprite,
  },
  {
    id: 'yorha-sit-2b',
    name: 'YoRHa Sit-2B',
    description: { zh: '坐姿编程伙伴，状态变化克制。', en: 'A seated coding companion with restrained motion.' },
    spriteUrl: yorhaSprite,
  },
];

export const petRows: Record<PetAnimationState, { row: number; frames: number; fps: number }> = {
  idle: { row: 0, frames: 6, fps: 6 },
  'running-right': { row: 1, frames: 8, fps: 8 },
  'running-left': { row: 2, frames: 8, fps: 8 },
  waving: { row: 3, frames: 4, fps: 6 },
  jumping: { row: 4, frames: 5, fps: 7 },
  failed: { row: 5, frames: 8, fps: 7 },
  waiting: { row: 6, frames: 6, fps: 6 },
  running: { row: 7, frames: 6, fps: 8 },
  review: { row: 8, frames: 6, fps: 6 },
};

export function petById(id: PetId, customPets: CustomPet[] = []): PetCatalogEntry {
  return petCatalog.find(pet => pet.id === id) ?? customPets.find(pet => pet.id === id) ?? petCatalog[0];
}
