export interface DesktopPerfMetrics {
  longThread: {
    totalMessages: number;
    initialMountedRows: number;
    expandedMountedRows: number;
    initialWindow: string;
    expandedWindow: string;
    expandMs: number;
  };
  streamBurst: {
    chunks: number;
    finalChars: number;
    durationMs: number;
    mountedRows: number;
  };
  panelMotion: {
    frames: number;
    averageFrameMs: number;
    p95FrameMs: number;
    maxFrameMs: number;
    droppedFramesOver32Ms: number;
    durationMs: number;
    toggles: number;
  };
  composerInput: {
    chars: number;
    updates: number;
    durationMs: number;
  };
  markdownHeavy: {
    chars: number;
    tables: number;
    codeBlocks: number;
    renderMs: number;
    mountedRows: number;
  };
  toolOutputHeavy: {
    toolCards: number;
    resultChars: number;
    renderMs: number;
    expandMs: number;
  };
  rightRailToolPreview: {
    chars: number;
    lines: number;
    renderMs: number;
  };
  transcriptReplay: {
    events: number;
    finalMessages: number;
    totalMs: number;
    p95FrameMs: number;
    mountedRows: number;
    droppedFramesOver32Ms: number;
    rightRailPreviewMs: number;
  };
}

export interface DesktopPerfBudgets {
  longThreadInitialRowsMax: number;
  longThreadExpandedRowsMax: number;
  longThreadExpandMsMax: number;
  streamBurstMountedRowsMax: number;
  streamBurstDurationMsMax: number;
  panelP95FrameMsMax: number;
  panelDroppedFramesOver32Max: number;
  composerInputDurationMsMax: number;
  markdownHeavyRenderMsMax: number;
  markdownHeavyMountedRowsMax: number;
  toolOutputCardCountMin: number;
  toolOutputRenderMsMax: number;
  toolCardExpandMsMax: number;
  rightRailToolPreviewMsMax: number;
  transcriptReplayTotalMsMax: number;
  transcriptReplayP95FrameMsMax: number;
  transcriptReplayMountedRowsMax: number;
  transcriptReplayDroppedFramesOver32Max: number;
  transcriptReplayRightRailPreviewMsMax: number;
}

export interface DesktopPerfBudgetResult {
  id: keyof DesktopPerfBudgets;
  actual: number;
  limit: number;
  ok: boolean;
  detail: string;
}

export const DESKTOP_PERF_BUDGETS: DesktopPerfBudgets = {
  longThreadInitialRowsMax: 90,
  longThreadExpandedRowsMax: 130,
  longThreadExpandMsMax: 250,
  streamBurstMountedRowsMax: 4,
  streamBurstDurationMsMax: 500,
  panelP95FrameMsMax: 40,
  panelDroppedFramesOver32Max: 2,
  composerInputDurationMsMax: 250,
  markdownHeavyRenderMsMax: 400,
  markdownHeavyMountedRowsMax: 4,
  toolOutputCardCountMin: 24,
  toolOutputRenderMsMax: 450,
  toolCardExpandMsMax: 300,
  rightRailToolPreviewMsMax: 300,
  transcriptReplayTotalMsMax: 900,
  transcriptReplayP95FrameMsMax: 45,
  transcriptReplayMountedRowsMax: 90,
  transcriptReplayDroppedFramesOver32Max: 2,
  transcriptReplayRightRailPreviewMsMax: 300,
};

export function evaluateDesktopPerfBudgets(
  metrics: DesktopPerfMetrics,
  budgets: DesktopPerfBudgets = DESKTOP_PERF_BUDGETS,
): DesktopPerfBudgetResult[] {
  return [
    result('longThreadInitialRowsMax', metrics.longThread.initialMountedRows, budgets.longThreadInitialRowsMax),
    result('longThreadExpandedRowsMax', metrics.longThread.expandedMountedRows, budgets.longThreadExpandedRowsMax),
    result('longThreadExpandMsMax', metrics.longThread.expandMs, budgets.longThreadExpandMsMax, 'ms'),
    result('streamBurstMountedRowsMax', metrics.streamBurst.mountedRows, budgets.streamBurstMountedRowsMax),
    result('streamBurstDurationMsMax', metrics.streamBurst.durationMs, budgets.streamBurstDurationMsMax, 'ms'),
    result('panelP95FrameMsMax', metrics.panelMotion.p95FrameMs, budgets.panelP95FrameMsMax, 'ms'),
    result(
      'panelDroppedFramesOver32Max',
      metrics.panelMotion.droppedFramesOver32Ms,
      budgets.panelDroppedFramesOver32Max,
    ),
    result('composerInputDurationMsMax', metrics.composerInput.durationMs, budgets.composerInputDurationMsMax, 'ms'),
    result('markdownHeavyRenderMsMax', metrics.markdownHeavy.renderMs, budgets.markdownHeavyRenderMsMax, 'ms'),
    result('markdownHeavyMountedRowsMax', metrics.markdownHeavy.mountedRows, budgets.markdownHeavyMountedRowsMax),
    minResult('toolOutputCardCountMin', metrics.toolOutputHeavy.toolCards, budgets.toolOutputCardCountMin),
    result('toolOutputRenderMsMax', metrics.toolOutputHeavy.renderMs, budgets.toolOutputRenderMsMax, 'ms'),
    result('toolCardExpandMsMax', metrics.toolOutputHeavy.expandMs, budgets.toolCardExpandMsMax, 'ms'),
    result(
      'rightRailToolPreviewMsMax',
      metrics.rightRailToolPreview.renderMs,
      budgets.rightRailToolPreviewMsMax,
      'ms',
    ),
    result('transcriptReplayTotalMsMax', metrics.transcriptReplay.totalMs, budgets.transcriptReplayTotalMsMax, 'ms'),
    result(
      'transcriptReplayP95FrameMsMax',
      metrics.transcriptReplay.p95FrameMs,
      budgets.transcriptReplayP95FrameMsMax,
      'ms',
    ),
    result(
      'transcriptReplayMountedRowsMax',
      metrics.transcriptReplay.mountedRows,
      budgets.transcriptReplayMountedRowsMax,
    ),
    result(
      'transcriptReplayDroppedFramesOver32Max',
      metrics.transcriptReplay.droppedFramesOver32Ms,
      budgets.transcriptReplayDroppedFramesOver32Max,
    ),
    result(
      'transcriptReplayRightRailPreviewMsMax',
      metrics.transcriptReplay.rightRailPreviewMs,
      budgets.transcriptReplayRightRailPreviewMsMax,
      'ms',
    ),
  ];
}

function result(id: keyof DesktopPerfBudgets, actual: number, limit: number, unit = ''): DesktopPerfBudgetResult {
  const ok = actual <= limit;
  const suffix = unit ? ` ${unit}` : '';
  return {
    id,
    actual,
    limit,
    ok,
    detail: `${id}: ${actual}${suffix} <= ${limit}${suffix}`,
  };
}

function minResult(id: keyof DesktopPerfBudgets, actual: number, limit: number, unit = ''): DesktopPerfBudgetResult {
  const ok = actual >= limit;
  const suffix = unit ? ` ${unit}` : '';
  return {
    id,
    actual,
    limit,
    ok,
    detail: `${id}: ${actual}${suffix} >= ${limit}${suffix}`,
  };
}
