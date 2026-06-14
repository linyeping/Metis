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
});
