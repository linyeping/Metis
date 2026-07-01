import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { AlertTriangle, Search } from 'lucide-react';
import { searchSessions } from '../../lib/api';
import { navigateToSession } from '../../lib/modeNavigation';
import type { AppMode, SearchResult } from '../../lib/types';
import { useSessionStore } from '../../store/sessionStore';
import { useUiStore } from '../../store/uiStore';
import { useT } from '../../hooks/useT';

export function SessionSearch() {
  const t = useT();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const sessions = useSessionStore(state => state.sessions);
  const workspaces = useSessionStore(state => state.workspaces);
  const appMode = useUiStore(state => state.appMode);

  const enriched = useMemo(
    () => enrichSearchResults(results, sessions, workspaces),
    [results, sessions, workspaces],
  );

  useEffect(() => {
    const value = query.trim();
    setError('');
    if (value.length < 2) {
      setResults([]);
      setLoading(false);
      return undefined;
    }
    const handle = window.setTimeout(() => {
      setLoading(true);
      void searchSessions(value)
        .then(setResults)
        .catch(err => {
          setResults([]);
          setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => setLoading(false));
    }, 220);
    return () => window.clearTimeout(handle);
  }, [query]);

  const openResult = (result: SearchResult) => {
    const targetMode = result.mode || appMode;
    navigateToSession(result.sessionId, targetMode);
    setQuery('');
    setResults([]);
  };

  const active = query.trim().length > 0;

  return (
    <div className="session-search" data-active={active}>
      <label>
        <Search size={14} />
        <input value={query} placeholder={t('搜索会话全文')} onChange={event => setQuery(event.target.value)} />
      </label>
      {active && (
        <div className="session-search-results">
          <header>
            <strong>{query.trim().length < 2 ? t('继续输入') : loading ? t('搜索中') : `${enriched.length} ${t('个结果')}`}</strong>
            <span>FTS</span>
          </header>
          {query.trim().length < 2 && <p>{t('至少输入 2 个字符。')}</p>}
          {loading && <p>{t('搜索中...')}</p>}
          {error && (
            <p className="session-search-error">
              <AlertTriangle size={13} />
              {error}
            </p>
          )}
          {!loading && !error && query.trim().length >= 2 && enriched.length === 0 && <p>{t('没有结果')}</p>}
          {!loading &&
            !error &&
            enriched.map(result => (
              <button key={result.sessionId} type="button" onClick={() => openResult(result)}>
                <strong>{result.title || 'Metis Chat'}</strong>
                <span>{renderSnippet(result.snippet)}</span>
                <small>
                  {t(result.workspaceName || '当前工作区')}
                  {result.ts ? ` · ${formatDate(result.ts)}` : ''}
                </small>
              </button>
            ))}
        </div>
      )}
    </div>
  );
}

export function enrichSearchResults(
  results: SearchResult[],
  sessions: Array<{ id: string; workspaceId: string; mode?: string }>,
  workspaces: Array<{ id: string; name: string }>,
): SearchResult[] {
  const sessionWorkspace = new Map(sessions.map(session => [session.id, session.workspaceId]));
  const sessionModes = new Map<string, AppMode>();
  for (const session of sessions) {
    const mode = toAppMode(session.mode || '');
    if (mode) sessionModes.set(session.id, mode);
  }
  const workspaceNames = new Map(workspaces.map(workspace => [workspace.id, workspace.name]));
  return results.map(result => {
    const workspaceId = result.workspaceId || sessionWorkspace.get(result.sessionId) || '';
    return {
      ...result,
      mode: result.mode || sessionModes.get(result.sessionId) || undefined,
      workspaceId,
      workspaceName: result.workspaceName || workspaceNames.get(workspaceId) || '',
    };
  });
}

function toAppMode(value: string): AppMode | null {
  return value === 'chat' || value === 'cowork' || value === 'code' ? value : null;
}

export function renderSnippet(snippet: string) {
  const nodes: ReactNode[] = [];
  let marked = false;
  for (const token of snippet.split(/(<mark>|<\/mark>)/)) {
    if (!token) continue;
    if (token === '<mark>') {
      marked = true;
      continue;
    }
    if (token === '</mark>') {
      marked = false;
      continue;
    }
    nodes.push(marked ? <mark key={`${token}-${nodes.length}`}>{token}</mark> : token);
  }
  return nodes;
}

function formatDate(ts: number): string {
  const value = ts > 10_000_000_000 ? ts : ts * 1000;
  return new Date(value).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}
