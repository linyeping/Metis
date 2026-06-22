import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react';
import { useEffect } from 'react';
import { useUiStore, type ToastNotice } from '../../store/uiStore';

export function ToastViewport() {
  const toasts = useUiStore(state => state.toasts);
  const dismissToast = useUiStore(state => state.dismissToast);

  return (
    <div className="toast-viewport" aria-live="polite" aria-atomic="false">
      {toasts.map(toast => (
        <ToastItem dismissToast={dismissToast} key={toast.id} toast={toast} />
      ))}
    </div>
  );
}

function ToastItem({
  dismissToast,
  toast,
}: {
  dismissToast: (id: string) => void;
  toast: ToastNotice;
}) {
  useEffect(() => {
    if (toast.duration === 0) return undefined;
    const timer = window.setTimeout(() => dismissToast(toast.id), toast.duration ?? 5200);
    return () => window.clearTimeout(timer);
  }, [dismissToast, toast.duration, toast.id]);

  const Icon = toast.type === 'success' ? CheckCircle2 : toast.type === 'info' ? Info : AlertTriangle;

  return (
    <section className="toast-card" data-type={toast.type} role={toast.type === 'error' ? 'alert' : 'status'}>
      <span className="toast-icon">
        <Icon size={16} />
      </span>
      <div className="toast-copy">
        <strong>{toast.title}</strong>
        {toast.description && <p>{toast.description}</p>}
        {toast.action &&
          (toast.onAction ? (
            <button type="button" className="toast-action" onClick={() => toast.onAction?.()}>
              {toast.action}
            </button>
          ) : (
            <small>{toast.action}</small>
          ))}
      </div>
      <button className="toast-close" type="button" aria-label="关闭通知" onClick={() => dismissToast(toast.id)}>
        <X size={14} />
      </button>
    </section>
  );
}
