import type { RuntimeAgentDef } from '../types.js';

export const metisAgentDef = {
  id: 'metis',
  name: 'Metis',
  bin: 'metis-native',
  versionArgs: [],
  fallbackModels: [{ id: 'default', label: 'Metis model settings', default: true }],
  buildArgs: () => [],
  streamFormat: 'metis-http',
  transport: 'metis-http',
  supportsCustomModel: false,
  supportsImagePaths: true,
} satisfies RuntimeAgentDef;
