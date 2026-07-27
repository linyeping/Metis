import { useEffect, useRef, useState } from 'react';
import { ThinkingOrb, type OrbSize, type OrbState } from 'thinking-orbs';
import { useUiStore } from '../../store/uiStore';
import { orbSpeedForRepositoryDemo } from '../../lib/orbState';

const MIN_STATE_DURATION_MS = 180;

interface MetisThinkingOrbProps {
  state: OrbState;
  label: string;
  size?: OrbSize;
}

export function MetisThinkingOrb({ state, label, size = 20 }: MetisThinkingOrbProps) {
  const appearanceMode = useUiStore(value => value.appearanceMode);
  const displayedState = useStableOrbState(state);

  return (
    <ThinkingOrb
      aria-label={label}
      className="metis-thinking-orb"
      role="img"
      size={size}
      speed={orbSpeedForRepositoryDemo(displayedState, size)}
      state={displayedState}
      theme={appearanceMode}
    />
  );
}

function useStableOrbState(nextState: OrbState): OrbState {
  const [displayedState, setDisplayedState] = useState(nextState);
  const lastAppliedAt = useRef(Date.now());

  useEffect(() => {
    if (nextState === displayedState) return undefined;
    const elapsed = Date.now() - lastAppliedAt.current;
    const delay = Math.max(0, MIN_STATE_DURATION_MS - elapsed);
    const timer = window.setTimeout(() => {
      lastAppliedAt.current = Date.now();
      setDisplayedState(nextState);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [displayedState, nextState]);

  return displayedState;
}
