import { useEffect, useMemo, useRef } from 'react';
import type { PetAnimationState } from '../lib/types';
import { useChatStore } from '../store/chatStore';
import { derivePetState } from './state';

export function PetStateBridge() {
  const compacting = useChatStore(state => state.compacting);
  const error = useChatStore(state => state.error);
  const runtimeStatus = useChatStore(state => state.runtimeStatus);
  const streaming = useChatStore(state => state.streaming);
  const subagents = useChatStore(state => state.subagents);
  const previousStreaming = useRef(false);
  const latestState = useRef<PetAnimationState>('idle');

  const state = useMemo(
    () => derivePetState({ compacting, error, runtimeStatus, streaming, subagents }),
    [compacting, error, runtimeStatus, streaming, subagents],
  );

  useEffect(() => {
    latestState.current = state;
    const completed = previousStreaming.current && !streaming && state === 'idle';
    previousStreaming.current = streaming;
    if (!window.metis?.petSetState) return undefined;
    if (!completed) {
      void window.metis.petSetState(state);
      return undefined;
    }
    void window.metis.petSetState('jumping');
    const timer = window.setTimeout(() => {
      void window.metis.petSetState(latestState.current);
    }, 2200);
    return () => window.clearTimeout(timer);
  }, [state, streaming]);

  return null;
}
