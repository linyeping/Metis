import { describe, expect, it } from 'vitest';
import { agentEventV2ToChatStreamEvent } from '../agentEventV2';
import { normalizeChatStreamEvent } from '../agentEvents';

function v2(kind: string, payload: Record<string, unknown>, seq = 1) {
  return {
    schema: 'metis.agent_event.v2',
    version: 2,
    run_id: 'run-1',
    session_id: 'session-1',
    turn_id: 'turn-run-1',
    message_id: 'assistant-1',
    seq,
    event_id: `evt_run-1_${String(seq).padStart(6, '0')}`,
    timestamp: '2026-07-06T00:00:00.000Z',
    kind,
    payload,
  };
}

describe('agentEventV2ToChatStreamEvent', () => {
  it('maps message deltas into the existing content delta event', () => {
    const event = agentEventV2ToChatStreamEvent(v2('message_delta', { text: 'hello' }));
    expect(event.type).toBe('content_delta');
    expect(event.seq).toBe(1);
    expect(normalizeChatStreamEvent(event).text).toBe('hello');
  });

  it('maps tool lifecycle events by backend call_id', () => {
    const requested = normalizeChatStreamEvent(
      agentEventV2ToChatStreamEvent(
        v2('tool_requested', {
          call_id: 'call-1',
          tool_name: 'read_file',
          arguments: { path: 'x.py' },
          summary: 'Read x.py',
        }),
      ),
    );
    const succeeded = normalizeChatStreamEvent(
      agentEventV2ToChatStreamEvent(
        v2('tool_succeeded', {
          call_id: 'call-1',
          tool_name: 'read_file',
          result: 'ok',
          summary: 'Read complete',
        }),
      ),
    );

    expect(requested.kind).toBe('tool_call');
    expect(requested.callId).toBe('call-1');
    expect(requested.toolName).toBe('read_file');
    expect(requested.args).toEqual({ path: 'x.py' });
    expect(succeeded.kind).toBe('tool_result');
    expect(succeeded.callId).toBe('call-1');
    expect(succeeded.result).toBe('ok');
  });

  it('maps failed tool terminals to error-shaped tool results', () => {
    const failed = normalizeChatStreamEvent(
      agentEventV2ToChatStreamEvent(
        v2('tool_failed', {
          call_id: 'call-2',
          tool_name: 'write_file',
          error: { code: 'PERMISSION_DENIED', message: 'User denied execution' },
        }),
      ),
    );

    expect(failed.kind).toBe('tool_result');
    expect(failed.callId).toBe('call-2');
    expect(String(failed.result)).toContain('Error: User denied execution');
  });

  it('maps permission_required into the existing permission request event', () => {
    const event = normalizeChatStreamEvent(
      agentEventV2ToChatStreamEvent(
        v2('permission_required', {
          call_id: 'call-3',
          request_id: 'perm-1',
          tool_name: 'write_file',
          arguments: { path: 'x.py' },
          permission: {
            schema: 'metis.permission_request.v1',
            status: 'requested',
            can_grant_full_access: true,
          },
        }),
      ),
    );

    expect(event.kind).toBe('permission_request');
    expect(event.callId).toBe('call-3');
    expect(event.requestId).toBe('perm-1');
    expect(event.permission?.canGrantFullAccess).toBe(true);
  });

  it('maps permission lifecycle updates into runtime status events', () => {
    const event = normalizeChatStreamEvent(
      agentEventV2ToChatStreamEvent(
        v2('permission_applied', {
          call_id: 'call-3',
          tool_name: 'write_file',
          permission: {
            schema: 'metis.permission_request.v1',
            status: 'applied',
          },
        }),
      ),
    );

    expect(event.kind).toBe('runtime_status');
    expect(event.runtimeStatus?.phase).toBe('permission_applied');
    expect(event.runtimeStatus?.callId).toBe('call-3');
  });

  it('keeps cowork subrun lifecycle as first-class activity events', () => {
    const running = normalizeChatStreamEvent(
      agentEventV2ToChatStreamEvent(
        v2('subrun_running', {
          schema: 'metis.cowork_subrun_event.v1',
          version: 1,
          subrun_id: 'subrun-1',
          title: 'Inspect implementation',
          status: 'running',
          progress: 40,
          stage: 'agent_running',
          execution_profile: 'local_worktree',
          worktree_id: 'wt_1',
        }),
      ),
    );
    const succeeded = normalizeChatStreamEvent(
      agentEventV2ToChatStreamEvent(
        v2('subrun_succeeded', {
          schema: 'metis.cowork_subrun_event.v1',
          version: 1,
          subrun_id: 'subrun-1',
          title: 'Inspect implementation',
          status: 'succeeded',
          progress: 100,
          result: { ok: true },
        }),
      ),
    );

    expect(running.kind).toBe('subrun_running');
    expect(running.subagent).toMatchObject({
      taskId: 'subrun-1',
      name: 'Inspect implementation',
      status: 'running',
      progress: 40,
      source: 'cowork_subrun',
      stage: 'agent_running',
      executionProfile: 'local_worktree',
      worktreeId: 'wt_1',
    });
    expect(succeeded.subagent).toMatchObject({
      taskId: 'subrun-1',
      status: 'done',
      progress: 100,
      source: 'cowork_subrun',
    });
  });

  it('maps run_completed into done with usage preserved', () => {
    const event = normalizeChatStreamEvent(
      agentEventV2ToChatStreamEvent(
        v2('run_completed', {
          usage: {
            prompt_tokens: 10,
            completion_tokens: 2,
            total_tokens: 12,
          },
        }),
      ),
    );

    expect(event.kind).toBe('done');
    expect(event.usage?.totalTokens).toBe(12);
  });
});
