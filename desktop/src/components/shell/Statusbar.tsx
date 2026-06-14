import { Circle, Folder, Terminal } from 'lucide-react';
import { useChatStore } from '../../store/chatStore';
import { useSessionStore } from '../../store/sessionStore';
import { useUiStore } from '../../store/uiStore';
import { compactPythonPath } from '../../lib/chatUtils';
import { useT } from '../../hooks/useT';

interface StatusbarProps {
  backendReady: boolean;
  reconnect?: { attempt: number; limit: number } | null;
  model: string;
  pythonPath?: string;
}

export function Statusbar({ backendReady, reconnect, model, pythonPath }: StatusbarProps) {
  const t = useT();
  const streaming = useChatStore(state => state.streaming);
  const usage = useChatStore(state => state.usage);
  const workspaces = useSessionStore(state => state.workspaces);
  const activeWorkspaceId = useSessionStore(state => state.activeWorkspaceId);
  const setModelPickerOpen = useUiStore(state => state.setModelPickerOpen);
  const workspace = workspaces.find(item => item.id === activeWorkspaceId);
  const cacheTotal = (usage?.promptCacheHitTokens || 0) + (usage?.promptCacheMissTokens || 0);
  const cachePercent = cacheTotal > 0 ? Math.round(((usage?.promptCacheHitTokens || 0) / cacheTotal) * 100) : 0;
  const gaveUp = Boolean(reconnect && reconnect.limit > 0 && reconnect.attempt >= reconnect.limit && !backendReady);
  // 进程假死时 backendReady 仍为 true，但只要在重连就该如实显示（重连优先于"已连接"）。
  const reconnecting = Boolean(reconnect && reconnect.attempt > 0 && !gaveUp);
  const connectionLabel = gaveUp
    ? t('连接失败')
    : reconnecting
      ? `${t('正在重新连接')} (${reconnect!.attempt}/${reconnect!.limit})`
      : backendReady
        ? t('已连接')
        : t('连接中');

  return (
    <footer className="statusbar">
      <span className="status-item" data-connection={gaveUp ? 'failed' : reconnecting ? 'reconnecting' : backendReady ? 'ok' : 'connecting'}>
        <Circle size={9} fill={backendReady && !reconnecting ? 'currentColor' : 'none'} />
        {connectionLabel}
      </span>
      <span className="status-item">{streaming ? t('生成中') : t('就绪')}</span>
      <button type="button" className="status-item status-button" onClick={() => setModelPickerOpen(true)}>
        {model || 'deepseek-v4-flash'}
      </button>
      {workspace && (
        <span className="status-item path">
          <Folder size={12} />
          {workspace.path}
        </span>
      )}
      {pythonPath && (
        <span className="status-item python-env" title={pythonPath}>
          <Terminal size={11} />
          {compactPythonPath(pythonPath)}
        </span>
      )}
      {usage && <span className="status-item">tokens {usage.totalTokens}{cacheTotal > 0 ? ` cache ${cachePercent}%` : ''}</span>}
    </footer>
  );
}
