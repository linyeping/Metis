import { Check, ChevronDown, Pencil, Play, Plus, RotateCcw, Square, Trash2 } from 'lucide-react';
import { motion } from 'framer-motion';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import type { TerminalEventPayload, TerminalShell } from '../../lib/types';
import { getSettings } from '../../lib/api';
import { useSessionStore } from '../../store/sessionStore';
import { useUiStore } from '../../store/uiStore';

const LIVE_BUFFER_LIMIT = 120000;
const terminalShellOptions: Array<{ value: TerminalShell; label: string; prompt: string }> = [
  { value: 'powershell', label: 'PowerShell', prompt: 'PS>' },
  { value: 'cmd', label: 'cmd', prompt: '>' },
  { value: 'bash', label: 'bash', prompt: '$' },
  { value: 'sh', label: 'sh', prompt: '$' },
  { value: 'shell', label: 'System shell', prompt: '$' },
];

type TerminalStatus = 'idle' | 'starting' | 'ready' | 'exited';

interface TerminalPanelProps {
  embedded?: boolean;
  onRequestClose?: () => void;
}

interface LocalTerminal {
  backend: 'pty' | 'shell';
  cwd: string;
  localId: string;
  output: string;
  sessionId: string;
  shell: TerminalShell;
  status: TerminalStatus;
  title: string;
}

function createLocalTerminal(index: number, shell: TerminalShell): LocalTerminal {
  return {
    backend: 'shell',
    cwd: '',
    localId: `terminal-${Date.now()}-${index}`,
    output: '',
    sessionId: '',
    shell,
    status: 'idle',
    title: `Terminal ${index}`,
  };
}

export function TerminalPanel({ embedded = false, onRequestClose }: TerminalPanelProps) {
  const terminalOpen = useUiStore(state => state.terminalOpen);
  const terminalHeight = useUiStore(state => state.terminalHeight);
  const setTerminalHeight = useUiStore(state => state.setTerminalHeight);
  const setTerminalOpen = useUiStore(state => state.setTerminalOpen);
  const workspaces = useSessionStore(state => state.workspaces);
  const activeWorkspaceId = useSessionStore(state => state.activeWorkspaceId);
  const workspace = workspaces.find(item => item.id === activeWorkspaceId);
  const [command, setCommand] = useState('');
  const [defaultShell, setDefaultShell] = useState<TerminalShell>('powershell');
  const [terminals, setTerminals] = useState<LocalTerminal[]>(() => [createLocalTerminal(1, 'powershell')]);
  const [activeTerminalId, setActiveTerminalId] = useState(() => terminals[0].localId);
  const [menuOpen, setMenuOpen] = useState(false);
  const [renamingTerminalId, setRenamingTerminalId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const outputRef = useRef<HTMLDivElement | null>(null);
  const commandInputRef = useRef<HTMLInputElement | null>(null);
  const dragStart = useRef<{ y: number; height: number } | null>(null);
  const terminalsRef = useRef(terminals);
  const nextTerminalIndex = useRef(2);
  const panelOpen = embedded || terminalOpen;

  useEffect(() => {
    terminalsRef.current = terminals;
  }, [terminals]);

  useEffect(
    () => () => {
      for (const terminal of terminalsRef.current) {
        if (terminal.sessionId) void window.metis.terminalKill(terminal.sessionId).catch(() => ({ ok: false }));
      }
    },
    [],
  );

  const activeTerminal = useMemo(
    () => terminals.find(terminal => terminal.localId === activeTerminalId) || terminals[0],
    [activeTerminalId, terminals],
  );
  const activeShell = activeTerminal?.shell || defaultShell;
  const activeStatus = activeTerminal?.status || 'idle';
  const activeSessionId = activeTerminal?.sessionId || '';
  const activeBackend = activeTerminal?.backend || 'shell';
  const terminalCwd = activeTerminal?.cwd || '';

  const updateTerminal = useCallback((localId: string, updater: (terminal: LocalTerminal) => LocalTerminal) => {
    setTerminals(current => current.map(terminal => (terminal.localId === localId ? updater(terminal) : terminal)));
  }, []);

  const appendOutput = useCallback(
    (localId: string, value: string) => {
      if (!value) return;
      updateTerminal(localId, terminal => {
        const next = `${terminal.output}${value}`;
        return {
          ...terminal,
          output: next.length > LIVE_BUFFER_LIMIT ? next.slice(next.length - LIVE_BUFFER_LIMIT) : next,
        };
      });
    },
    [updateTerminal],
  );

  const terminalSize = useCallback(() => {
    const outputEl = outputRef.current;
    const width = outputEl?.clientWidth || 760;
    const height = outputEl?.clientHeight || 180;
    return {
      cols: Math.max(40, Math.floor(width / 8)),
      rows: Math.max(8, Math.floor(height / 18)),
    };
  }, []);

  const startTerminal = useCallback(
    async (localId: string, options: { clear?: boolean; restart?: boolean } = {}) => {
      if (!panelOpen) return;
      const current = terminalsRef.current.find(terminal => terminal.localId === localId);
      if (!current) return;
      if (options.restart && current.sessionId) {
        await window.metis.terminalKill(current.sessionId).catch(() => ({ ok: false }));
      }
      updateTerminal(localId, terminal => ({
        ...terminal,
        output: options.clear ? '' : terminal.output,
        status: 'starting',
      }));
      const size = terminalSize();
      const session = await window.metis.terminalCreate({
        cwd: workspace?.path,
        shell: current.shell,
        cols: size.cols,
        rows: size.rows,
      });
      updateTerminal(localId, terminal => ({
        ...terminal,
        backend: session.backend,
        cwd: session.cwd,
        sessionId: session.id,
        shell: session.shell,
        status: 'ready',
      }));
      setActiveTerminalId(localId);
      setTimeout(() => commandInputRef.current?.focus(), 30);
    },
    [panelOpen, terminalSize, updateTerminal, workspace?.path],
  );

  useEffect(() => {
    if (!panelOpen) return;
    if (terminals.length > 0) return;
    const next = createLocalTerminal(nextTerminalIndex.current, defaultShell);
    nextTerminalIndex.current += 1;
    setTerminals([next]);
    setActiveTerminalId(next.localId);
  }, [defaultShell, panelOpen, terminals.length]);

  useEffect(() => {
    if (!panelOpen) return;
    setSettingsLoaded(false);
    void getSettings()
      .then(settings => {
        const nextShell = settings.terminalShell || 'powershell';
        setDefaultShell(nextShell);
        setTerminals(current =>
          current.map(terminal =>
            terminal.sessionId || terminal.status !== 'idle'
              ? terminal
              : {
                  ...terminal,
                  shell: nextShell,
                },
          ),
        );
      })
      .catch(() => null)
      .finally(() => setSettingsLoaded(true));
  }, [panelOpen]);

  useEffect(() => {
    const dispose = window.metis.onTerminalEvent((event: TerminalEventPayload) => {
      const target = terminalsRef.current.find(terminal => terminal.sessionId === event.id);
      if (!target) return;
      if (event.type === 'data') {
        appendOutput(target.localId, event.data || '');
      } else if (event.type === 'ready') {
        updateTerminal(target.localId, terminal => ({
          ...terminal,
          backend: event.backend || terminal.backend,
          cwd: event.cwd || terminal.cwd,
          shell: event.shell || terminal.shell,
          status: 'ready',
        }));
      } else if (event.type === 'error') {
        appendOutput(target.localId, `\r\n[terminal error] ${event.data || 'unknown error'}\r\n`);
      } else if (event.type === 'exit') {
        updateTerminal(target.localId, terminal => ({ ...terminal, status: 'exited' }));
      }
    });
    return dispose;
  }, [appendOutput, updateTerminal]);

  useEffect(() => {
    if (!panelOpen || !settingsLoaded || !activeTerminal || activeTerminal.sessionId || activeTerminal.status !== 'idle') return;
    void startTerminal(activeTerminal.localId, { clear: activeTerminal.output.length === 0 });
  }, [activeTerminal, panelOpen, settingsLoaded, startTerminal]);

  useEffect(() => {
    const outputEl = outputRef.current;
    if (!outputEl) return;
    outputEl.scrollTop = outputEl.scrollHeight;
  }, [activeTerminal?.output, panelOpen, activeTerminalId]);

  useEffect(() => {
    if (!activeSessionId) return;
    const size = terminalSize();
    void window.metis.terminalResize(activeSessionId, size.cols, size.rows);
  }, [activeSessionId, terminalHeight, terminalSize]);

  const createTerminal = (shell = defaultShell) => {
    const next = createLocalTerminal(nextTerminalIndex.current, shell);
    nextTerminalIndex.current += 1;
    setTerminals(current => [...current, next]);
    setActiveTerminalId(next.localId);
    setMenuOpen(false);
    setRenamingTerminalId(null);
    setCommand('');
  };

  const deleteTerminal = async (localId: string) => {
    const currentTerminals = terminalsRef.current;
    const target = currentTerminals.find(terminal => terminal.localId === localId);
    if (!target) return;
    if (target?.sessionId) await window.metis.terminalKill(target.sessionId).catch(() => ({ ok: false }));
    const remaining = currentTerminals.filter(terminal => terminal.localId !== localId);
    setMenuOpen(false);
    setRenamingTerminalId(null);
    setRenameDraft('');
    setCommand('');
    if (remaining.length > 0) {
      setTerminals(remaining);
      if (activeTerminalId === localId) setActiveTerminalId(remaining[0].localId);
      return;
    }
    setTerminals([]);
    setActiveTerminalId('');
    if (embedded) {
      onRequestClose?.();
    } else {
      setTerminalOpen(false);
    }
  };

  const renameTerminal = (localId: string) => {
    const nextTitle = renameDraft.trim().slice(0, 36);
    if (!nextTitle) {
      setRenamingTerminalId(null);
      return;
    }
    updateTerminal(localId, terminal => ({ ...terminal, title: nextTitle }));
    setRenamingTerminalId(null);
  };

  const startRenameTerminal = (terminal: LocalTerminal) => {
    setRenameDraft(terminal.title);
    setRenamingTerminalId(terminal.localId);
  };

  const sendInput = async () => {
    const text = command;
    if (!text.trim() || !activeSessionId) return;
    setCommand('');
    await window.metis.terminalInput(activeSessionId, `${text}\r\n`);
  };

  const sendCtrlC = async () => {
    if (!activeSessionId) return;
    await window.metis.terminalInput(activeSessionId, '\x03');
  };

  const focusCommandInput = () => {
    commandInputRef.current?.focus();
  };

  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const resizeTarget = event.currentTarget;
    try {
      resizeTarget.setPointerCapture(event.pointerId);
    } catch {}
    document.body.classList.add('resizing-terminal');
    dragStart.current = { y: event.clientY, height: terminalHeight };
    const handleMove = (moveEvent: PointerEvent) => {
      moveEvent.preventDefault();
      if (!dragStart.current) return;
      setTerminalHeight(dragStart.current.height - (moveEvent.clientY - dragStart.current.y));
    };
    const preventSelection = (selectEvent: Event) => {
      selectEvent.preventDefault();
    };
    const handleUp = () => {
      dragStart.current = null;
      document.body.classList.remove('resizing-terminal');
      document.removeEventListener('selectstart', preventSelection);
      try {
        resizeTarget.releasePointerCapture(event.pointerId);
      } catch {}
      window.removeEventListener('pointermove', handleMove);
      window.removeEventListener('pointerup', handleUp);
      window.removeEventListener('pointercancel', handleUp);
    };
    document.addEventListener('selectstart', preventSelection);
    window.addEventListener('pointermove', handleMove);
    window.addEventListener('pointerup', handleUp);
    window.addEventListener('pointercancel', handleUp);
  };

  const handleRenameKey = (event: KeyboardEvent<HTMLInputElement>, localId: string) => {
    if (event.key === 'Enter') renameTerminal(localId);
    if (event.key === 'Escape') setRenamingTerminalId(null);
  };

  const output = activeTerminal?.output || '';
  const prompt = terminalShellOptions.find(option => option.value === activeShell)?.prompt || '$';
  const statusLabel =
    activeStatus === 'ready' ? activeBackend.toUpperCase() : activeStatus === 'starting' ? '启动中' : activeStatus === 'exited' ? '已退出' : '未启动';

  const panelMotion = embedded
    ? {
        animate: { opacity: 1 },
        initial: false as const,
        transition: { duration: 0 },
      }
    : {
        animate: panelOpen
          ? { height: terminalHeight, opacity: 1, borderTopWidth: 1 }
          : { height: 0, opacity: 0, borderTopWidth: 0 },
        initial: false as const,
        transition: { type: 'spring' as const, stiffness: 300, damping: 26 },
      };

  return (
    <motion.section
      className="terminal-panel"
      data-embedded={embedded}
      data-open={panelOpen}
      aria-hidden={!panelOpen}
      layout={!embedded}
      style={{ '--terminal-height': `${terminalHeight}px` } as CSSProperties}
      {...panelMotion}
    >
      <div className="terminal-resizer" aria-label="拖拽调整终端高度" onPointerDown={startResize} />
      <header>
        <div className="terminal-tab-strip">
          <div className="terminal-menu-wrap">
            <button
              type="button"
              className="terminal-tab terminal-menu-trigger"
              title={terminalCwd || workspace?.path || ''}
              aria-expanded={menuOpen}
              onClick={() => {
                setMenuOpen(value => !value);
                setRenamingTerminalId(null);
              }}
            >
              <span className="terminal-status-dot" data-status={activeStatus} />
              <span>{activeTerminal?.title || 'Terminal'}</span>
              <ChevronDown size={12} />
            </button>
            {menuOpen && (
              <div className="terminal-menu" role="menu">
                <span className="terminal-menu-label">Terminals</span>
                {terminals.map(terminal => (
                  <div className="terminal-menu-row" data-active={terminal.localId === activeTerminalId} key ={terminal.localId}>
                    {renamingTerminalId === terminal.localId ? (
                      <div className="terminal-menu-main terminal-rename-row">
                        <span className="terminal-status-dot" data-status={terminal.status} />
                        <input
                          autoFocus
                          value={renameDraft}
                          onBlur={() => renameTerminal(terminal.localId)}
                          onChange={event => setRenameDraft(event.target.value)}
                          onKeyDown={event => handleRenameKey(event, terminal.localId)}
                        />
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="terminal-menu-main"
                        role="menuitemradio"
                        aria-checked={terminal.localId === activeTerminalId}
                        onClick={() => {
                          setActiveTerminalId(terminal.localId);
                          setCommand('');
                          setRenamingTerminalId(null);
                        }}
                      >
                        <span className="terminal-status-dot" data-status={terminal.status} />
                        <span>{terminal.title}</span>
                      </button>
                    )}
                    <span className="terminal-menu-check" data-visible={terminal.localId === activeTerminalId}>
                      {terminal.localId === activeTerminalId && <Check size={13} />}
                    </span>
                    <button
                      type="button"
                      className="terminal-menu-rename"
                      title={`重命名 ${terminal.title}`}
                      onClick={event => {
                        event.stopPropagation();
                        startRenameTerminal(terminal);
                      }}
                    >
                      <Pencil size={12} />
                    </button>
                    <button
                      type="button"
                      className="terminal-menu-delete"
                      title={`删除 ${terminal.title}`}
                      onClick={event => {
                        event.stopPropagation();
                        void deleteTerminal(terminal.localId);
                      }}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <button type="button" className="terminal-add-button" title="新建终端" onClick={() => createTerminal()}>
            <Plus size={13} />
          </button>
          <span className="terminal-live-status" data-status={activeStatus}>
            {statusLabel}
          </span>
        </div>
        <div className="terminal-control-group">
          <button type="button" title="发送 Ctrl+C" disabled={!activeSessionId} onClick={() => void sendCtrlC()}>
            <Square size={13} />
          </button>
          <button type="button" title="重启当前终端" onClick={() => activeTerminal && void startTerminal(activeTerminal.localId, { clear: false, restart: true })}>
            <RotateCcw size={13} />
          </button>
          <button type="button" title="清空输出" onClick={() => activeTerminal && updateTerminal(activeTerminal.localId, terminal => ({ ...terminal, output: '' }))}>
            <Trash2 size={13} />
          </button>
        </div>
      </header>
      <div className="terminal-output terminal-live-output" ref={outputRef} onClick={focusCommandInput}>
        {output ? <pre>{output}</pre> : <p>终端启动中...</p>}
      </div>
      <div className="terminal-input-row">
        <span>{prompt}</span>
        <input
          ref={commandInputRef}
          className="terminal-command-input"
          value={command}
          placeholder="npm run dev"
          onChange={event => setCommand(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'c' && event.ctrlKey) {
              event.preventDefault();
              void sendCtrlC();
              return;
            }
            if (event.key !== 'Enter' || event.shiftKey) return;
            event.preventDefault();
            void sendInput();
          }}
        />
        <button type="button" disabled={!command.trim() || activeStatus !== 'ready'} onClick={() => void sendInput()}>
          <Play size={13} />
          发送
        </button>
      </div>
    </motion.section>
  );
}
