import { AlertTriangle, ExternalLink, Info, Trash2, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { AppDialogIcon } from '../../store/uiStore';
import { useUiStore } from '../../store/uiStore';

export function AppDialog() {
  const dialog = useUiStore(state => state.appDialog);
  const closeDialog = useUiStore(state => state.closeAppDialog);
  const language = useUiStore(state => state.language);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const [choice, setChoice] = useState('');

  useEffect(() => {
    if (!dialog) return undefined;

    setChoice(dialog.defaultChoice || dialog.choices?.[0]?.value || '');
    const timer = window.setTimeout(() => confirmRef.current?.focus(), 20);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeDialog(false);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [dialog, closeDialog]);

  if (!dialog) return null;

  const Icon = dialogIcon(dialog.icon);
  const cancelLabel = dialog.cancelLabel || (language === 'zh' ? '取消' : 'Cancel');
  const confirmLabel = dialog.confirmLabel || (language === 'zh' ? '确认' : 'Confirm');

  return (
    <div className="modal-layer app-dialog-layer" role="presentation">
      <section
        className="app-dialog"
        data-tone={dialog.tone}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={`${dialog.id}-title`}
        aria-describedby={`${dialog.id}-message`}
      >
        <header>
          <span className="app-dialog-icon" data-tone={dialog.tone}>
            <Icon size={18} />
          </span>
          <div>
            <h2 id={`${dialog.id}-title`}>{dialog.title}</h2>
            <p>{dialog.tone === 'danger' ? (language === 'zh' ? '请确认这个操作' : 'Please confirm this action') : 'Metis'}</p>
          </div>
          <button className="app-dialog-close" type="button" aria-label={cancelLabel} onClick={() => closeDialog(false)}>
            <X size={16} />
          </button>
        </header>
        <div className="app-dialog-body" id={`${dialog.id}-message`}>
          <div className="app-dialog-message">
            {dialog.message.split(/\r?\n/).map((line, index) => (
              <p key={`${index}-${line}`}>{line || '\u00a0'}</p>
            ))}
          </div>
          {dialog.details && <pre className="app-dialog-details">{dialog.details}</pre>}
          {dialog.choices && dialog.choices.length > 0 && (
            <fieldset className="app-dialog-choices">
              {dialog.choices.map(option => (
                <label key={option.value}>
                  <input
                    type="radio"
                    name={`${dialog.id}-choice`}
                    value={option.value}
                    checked={choice === option.value}
                    onChange={() => setChoice(option.value)}
                  />
                  <span>
                    <strong>{option.label}</strong>
                    {option.description && <small>{option.description}</small>}
                  </span>
                </label>
              ))}
            </fieldset>
          )}
        </div>
        <footer>
          <button className="app-dialog-cancel-button" type="button" onClick={() => closeDialog(false, choice)}>
            {cancelLabel}
          </button>
          <button ref={confirmRef} className="app-dialog-confirm-button" type="button" onClick={() => closeDialog(true, choice)}>
            {confirmLabel}
          </button>
        </footer>
      </section>
    </div>
  );
}

function dialogIcon(icon: AppDialogIcon) {
  if (icon === 'external') return ExternalLink;
  if (icon === 'trash') return Trash2;
  if (icon === 'warning') return AlertTriangle;
  return Info;
}
