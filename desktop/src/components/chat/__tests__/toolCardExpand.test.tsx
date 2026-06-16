import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ToolCard } from '../ToolCallBlock';
import { useUiStore } from '../../../store/uiStore';

// 复现用户报告：「单步卡片点不开」。
// 直接挂载 ToolCard、模拟点击卡头，断言详情区域出现 + store 记录该卡展开。
describe('ToolCard 单步展开', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    useUiStore.getState().clearExpandedToolCards();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function renderCard(props: any) {
    act(() => root.render(<ToolCard {...props} />));
  }

  it('点击卡头后展开详情、收起后隐藏', () => {
    renderCard({
      toolCallId: 'call_1',
      toolName: 'desktop_action',
      args: { action: 'click', x: 100, y: 200 },
      result: 'Done: click',
      metisStatus: 'success',
    });

    // 初始：成功卡默认收起，无详情
    expect(container.querySelector('.tool-activity-details')).toBeNull();

    const head = container.querySelector('.tool-card-head') as HTMLButtonElement | null;
    expect(head).not.toBeNull();

    // 点击展开
    act(() => head!.click());
    expect(container.querySelector('.tool-activity-details')).not.toBeNull();
    expect(useUiStore.getState().expandedToolCards.has('call_1')).toBe(true);

    // 再点击收起
    const head2 = container.querySelector('.tool-card-head') as HTMLButtonElement;
    act(() => head2.click());
    expect(container.querySelector('.tool-activity-details')).toBeNull();
    expect(useUiStore.getState().expandedToolCards.has('call_1')).toBe(false);
  });

  it('参数相同但 callId 不同的两张卡，展开互不影响（防 id 撞车）', () => {
    const containerB = document.createElement('div');
    document.body.appendChild(containerB);
    const rootB = createRoot(containerB);

    renderCard({
      toolCallId: 'call_A',
      toolName: 'desktop_action',
      args: { action: 'click' },
      result: 'Done',
      metisStatus: 'success',
    });
    act(() =>
      rootB.render(
        <ToolCard
          {...({
            toolCallId: 'call_B',
            toolName: 'desktop_action',
            args: { action: 'click' },
            result: 'Done',
            metisStatus: 'success',
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
          } as any)}
        />,
      ),
    );

    const headA = container.querySelector('.tool-card-head') as HTMLButtonElement;
    act(() => headA.click());

    expect(useUiStore.getState().expandedToolCards.has('call_A')).toBe(true);
    expect(useUiStore.getState().expandedToolCards.has('call_B')).toBe(false);
    expect(containerB.querySelector('.tool-activity-details')).toBeNull();

    act(() => rootB.unmount());
    containerB.remove();
  });

  it('显示 verifier v2 证据链摘要', () => {
    renderCard({
      toolCallId: 'verify_1',
      toolName: 'preview_browser_verify',
      args: { assertion: '确认登录按钮可见并可点击' },
      result: {
        ok: true,
        evidence_schema: 'metis.verifier.evidence_chain.v2',
        verdict: {
          ok: true,
          summary: 'Verified: 2/2 checks passed. 确认登录按钮可见并可点击',
          passed: 2,
          failed: 0,
          total: 2,
        },
        checks: {
          button_visible: true,
          button_clickable: true,
        },
        evidence_chain_v2: [
          {
            kind: 'page',
            ok: true,
            summary: 'Observed Preview page: Metis Login',
          },
          {
            kind: 'check',
            check: 'button_clickable',
            ok: true,
            summary: 'button clickable: passed',
          },
        ],
      },
      metisStatus: 'success',
    });

    const summary = container.querySelector('.tool-browser-activity-summary')?.textContent || '';
    expect(summary).toContain('验收证据');
    expect(summary).toContain('Verified: 2/2 checks passed');
    expect(summary).toContain('button clickable: passed');
  });

  it('显示 code-to-report artifact 活动摘要和步骤', () => {
    renderCard({
      toolCallId: 'report_1',
      toolName: 'office_report_from_code_run',
      args: { output_path: 'output/docx/lab.docx' },
      result: {
        ok: true,
        schema: 'metis.artifact.code_report.v1',
        output_path: 'D:/workspace/output/docx/lab.docx',
        artifact_activity: {
          kind: 'code_to_report',
          summary: 'done: D:/workspace/output/docx/lab.docx; artifacts=2',
          output_path: 'D:/workspace/output/docx/lab.docx',
          artifacts: [
            { path: 'D:/workspace/output/report_artifacts/lab/plot.png', kind: 'image' },
            { path: 'D:/workspace/output/report_artifacts/lab/results.csv', kind: 'text' },
          ],
          items: [
            { event: 'write_code', title: 'Write Python script', ok: true, path: 'D:/workspace/output/report_artifacts/lab/analysis.py' },
            { event: 'run_code', title: 'Run code', ok: true, detail: 'exit 0 in 42ms' },
            { event: 'write_report', title: 'Write DOCX report', ok: true, path: 'D:/workspace/output/docx/lab.docx' },
          ],
        },
      },
      metisStatus: 'success',
    });

    const summary = container.querySelector('.tool-artifact-activity-summary')?.textContent || '';
    expect(summary).toContain('done: D:/workspace/output/docx/lab.docx');

    const head = container.querySelector('.tool-card-head') as HTMLButtonElement;
    act(() => head.click());

    const timeline = container.querySelector('.tool-artifact-activity-timeline')?.textContent || '';
    expect(timeline).toContain('报告活动');
    expect(timeline).toContain('Write Python script');
    expect(timeline).toContain('exit 0 in 42ms');
  });
});
