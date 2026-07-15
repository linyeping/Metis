export type DesignProjectKind = 'prototype' | 'deck' | 'template' | 'other';
export type DesignFidelity = 'wireframe' | 'high-fidelity';
export type DesignRuntimeStateName = 'idle' | 'starting' | 'ready' | 'error' | 'unavailable';
export type DesignViewStateName = 'hidden' | 'loading' | 'ready' | 'occluded' | 'error';

export interface DesignProjectSummary {
  id: string;
  name: string;
  kind: DesignProjectKind;
  fidelity: DesignFidelity;
  createdAt: number;
  updatedAt: number;
  status: string;
}

export interface DesignSystemSummary {
  id: string;
  title: string;
  description: string;
  source: string;
}

export interface CreateDesignProjectInput {
  name: string;
  kind: DesignProjectKind;
  fidelity: DesignFidelity;
  designSystemId?: string | null;
  prompt?: string;
}

export interface DesignRuntimeStatus {
  state: DesignRuntimeStateName;
  url: string;
  error: string;
  version: string;
  repository: string;
  sourceRoot: string;
  logs: string[];
  updatedAt?: string;
}

export interface DesignViewStatus {
  ok?: boolean;
  state: DesignViewStateName;
  url: string;
  title: string;
  visible: boolean;
  loading: boolean;
  error: string;
  bounds: { x: number; y: number; width: number; height: number } | null;
  updatedAt?: string;
}

export interface DesignProjectsResult {
  ok: boolean;
  projects: DesignProjectSummary[];
  error?: string;
}

export interface DesignSystemsResult {
  ok: boolean;
  systems: DesignSystemSummary[];
  error?: string;
}

export interface CreateDesignProjectResult {
  ok: boolean;
  project?: DesignProjectSummary;
  conversationId?: string;
  url?: string;
  error?: string;
}
