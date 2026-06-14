import { AlertTriangle, FileText, RefreshCw, Terminal } from 'lucide-react';
import logo from '../../assets/logo.png';
import type { BootEvent, BootState } from '../../lib/types';
import { useT } from '../../hooks/useT';

interface BootOverlayProps {
  state: BootState;
  onRetry: () => void;
  onOpenLog: () => void;
}

function eventText(event: BootEvent, t: (zh: string) => string): string {
  if (event.line) return event.line;
  if (event.title) return event.title;
  if (event.phase === 'ready' && event.port) return `${t('后端已就绪')}: 127.0.0.1:${event.port}`;
  return event.phase;
}

export function BootOverlay({ state, onRetry, onOpenLog }: BootOverlayProps) {
  const t = useT();
  const failed = state.status === 'error';
  const lines = state.events.map(event => eventText(event, t)).filter(Boolean).slice(-90);
  const detail = state.error?.detail || state.error?.logTail || '';

  return (
    <section className="boot-overlay" aria-live="polite">
      <div className="boot-card" data-error={failed}>
        <div className="boot-brand">
          <img src={logo} alt="" />
          <div>
            <span>Metis Desktop</span>
            <h1>{failed ? state.error?.title || t('后端启动失败') : t('正在启动 Metis 后端')}</h1>
          </div>
        </div>

        {!failed && (
          <div className="boot-progress">
            <span />
          </div>
        )}

        {failed && (
          <div className="boot-error-title">
            <AlertTriangle size={18} />
            <span>{t('启动失败，Metis 已尝试自动修复后端环境；下面是诊断信息')}</span>
          </div>
        )}

        {failed && detail && (
          <pre className="boot-error-detail">
            {detail}
          </pre>
        )}

        <div className="boot-log-head">
          <span>
            <Terminal size={15} />
            {t('后端日志')}
          </span>
          {state.logPath && <em>{state.logPath}</em>}
        </div>

        <pre className="boot-log">
          {lines.length ? lines.join('\n') : t('等待启动事件...')}
        </pre>

        {failed && (
          <div className="boot-actions">
            <button type="button" className="primary" onClick={onRetry}>
              <RefreshCw size={15} />
              {t('重试并重新修复')}
            </button>
            <button type="button" onClick={onOpenLog}>
              <FileText size={15} />
              {t('打开日志')}
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
