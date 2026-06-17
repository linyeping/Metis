import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), 'utf8');
}

function readSettingsSources() {
  return [
    'src/components/settings/SettingsDialog.tsx',
    'src/components/settings/FontSizeControl.tsx',
    'src/components/settings/PermissionPanel.tsx',
    'src/components/settings/settingsShared.ts',
    'src/components/settings/tabs/AppearanceTab.tsx',
    'src/components/settings/tabs/ConversationTab.tsx',
    'src/components/settings/tabs/ModelTab.tsx',
    'src/components/settings/tabs/UsageTab.tsx',
    'src/components/settings/tabs/NetworkTab.tsx',
    'src/components/settings/tabs/TerminalTab.tsx',
    'src/components/settings/tabs/ToolsTab.tsx',
    'src/components/settings/tabs/ConnectorsTab.tsx',
    'src/components/settings/tabs/DesktopTab.tsx',
    'src/components/settings/tabs/AboutTab.tsx',
  ].map(read).join('\n');
}

function listFiles(dir, result = []) {
  for (const entry of fs.readdirSync(path.join(root, dir), { withFileTypes: true })) {
    const relative = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      listFiles(relative, result);
    } else {
      result.push(relative);
    }
  }
  return result;
}

test('package exposes core verification scripts', () => {
  const pkg = JSON.parse(read('package.json'));
  assert.equal(pkg.scripts.typecheck, 'tsc --noEmit');
  assert.ok(pkg.scripts['smoke:desktop']);
  assert.ok(pkg.scripts['perf:desktop']);
  assert.ok(pkg.scripts['test:contracts']);
  assert.equal(pkg.scripts['test:fixed-regression'], 'node scripts/fixed-regression-runner.mjs');
  assert.equal(pkg.scripts['test:fixed-regression:list'], 'node scripts/fixed-regression-runner.mjs --list');
  assert.match(pkg.scripts.dist, /^npm run test:fixed-regression && /);
  assert.match(pkg.scripts['dist:win'], /^npm run test:fixed-regression && /);
  assert.ok(pkg.scripts['dist:win'].indexOf('test:fixed-regression') < pkg.scripts['dist:win'].indexOf('electron-builder'));
});

test('fixed regression runner covers agent safety and artifact gates', () => {
  const runner = read('scripts/fixed-regression-runner.mjs');

  for (const suite of [
    'permissions',
    'compact',
    'verifier',
    'browser-computer',
    'artifacts',
    'model-tools',
    'desktop-contracts',
  ]) {
    assert.match(runner, new RegExp(`id:\\s*'${suite}'`));
  }

  for (const testPath of [
    'backend/tests/test_permission_rules.py',
    'backend/tests/test_permission_control_plane.py',
    'backend/tests/test_fableadv_10_compaction_transcript_separation.py',
    'backend/tests/test_verifier_evidence_chain.py',
    'backend/tests/test_preview_browser_bridge.py',
    'backend/tests/test_win2_computer_use.py',
    'backend/tests/test_fableadv_20_computer_use.py',
    'backend/tests/test_artifact_pdf_docx_tools.py',
    'backend/tests/test_provider_registry.py',
    'backend/tests/test_agent_runtime_reliability.py',
    'backend/tests/test_deepseek_strict_schema.py',
    'scripts/desktop-contract-tests.mjs',
  ]) {
    assert.match(runner, new RegExp(testPath.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }

  assert.match(runner, /METIS_FIXED_REGRESSION_SUITE/);
  assert.match(runner, /--suite/);
  assert.match(runner, /--list/);
  assert.match(runner, /!arg\.startsWith\('-'\)/);
});

test('DeepSeek, slash menu, cache dashboard, and release gates stay wired', () => {
  const repoRoot = path.resolve(root, '..');
  const composer = read('src/components/chat/Composer.tsx');
  const css = read('src/index.css');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const openaiCompat = fs.readFileSync(path.join(repoRoot, 'backend', 'runtime', 'llm_backends', 'openai_compat.py'), 'utf8');
  const deepseekSchema = fs.readFileSync(path.join(repoRoot, 'backend', 'runtime', 'llm_backends', 'deepseek_schema.py'), 'utf8');
  const agentLoop = fs.readFileSync(path.join(repoRoot, 'backend', 'runtime', 'agent_loop.py'), 'utf8');

  assert.match(composer, /slashMenuRef/);
  assert.match(composer, /scrollIntoView\(\{ block: 'nearest' \}\)/);
  assert.match(css, /\.slash-menu\s*\{[\s\S]*max-height:\s*min\(360px,\s*calc\(100vh - 220px\)\)/);
  assert.match(css, /\.slash-menu\s*\{[\s\S]*overflow-y:\s*auto/);

  assert.match(openaiCompat, /sanitize_deepseek_strict_tools/);
  assert.match(openaiCompat, /_provider_tools/);
  assert.match(deepseekSchema, /function\["strict"\]\s*=\s*True/);
  assert.match(deepseekSchema, /additionalProperties"\]\s*=\s*False/);

  assert.match(agentLoop, /MAX_TOOL_CALL_REPAIR_ATTEMPTS/);
  assert.match(agentLoop, /tool_call_repair/);
  assert.match(agentLoop, /native tool\/function call/);

  assert.match(rightRail, /contextLedger/);
  assert.match(rightRail, /cacheHitRate/);
  assert.match(rightRail, /label=\{t\('Cache'\)\}/);
  assert.match(css, /grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(72px,\s*1fr\)\)/);
});

test('FABLEADV-47 connector OAuth stores tokens safely', () => {
  const oauth = read('electron/oauth.cjs');
  const main = read('electron/main.cjs');
  const preload = read('electron/preload.cjs');
  const globals = read('src/global.d.ts');
  const settings = readSettingsSources();

  assert.match(oauth, /safeStorage\.encryptString/);
  assert.match(oauth, /metis:connector-authorize/);
  assert.match(oauth, /metis:connector-status/);
  assert.match(oauth, /metis:connector-disconnect/);
  assert.match(oauth, /GITHUB_PERSONAL_ACCESS_TOKEN/);
  assert.match(oauth, /GOOGLE_OAUTH_ACCESS_TOKEN/);
  assert.match(oauth, /login\/device\/code/);
  assert.match(oauth, /code_challenge_method', 'S256'/);
  assert.doesNotMatch(oauth, /console\.log\(.*token/i);
  assert.match(main, /registerConnectorIpc/);
  assert.match(preload, /connectorAuthorize/);
  assert.match(preload, /connectorStatus/);
  assert.match(preload, /connectorDisconnect/);
  assert.match(globals, /connectorAuthorize/);
  assert.match(settings, /ConnectorsTab/);
});

test('FABLEADV-11 build hygiene keeps Vite and backend packaging scoped', () => {
  const vite = read('vite.config.ts');
  const buildScript = read('scripts/build-backend.ps1');
  const spec = read('scripts/build-backend.spec');
  const gitignore = fs.readFileSync(path.resolve(root, '..', '.gitignore'), 'utf8');
  const requirementsBuild = fs.readFileSync(path.resolve(root, '..', 'backend', 'requirements-build.txt'), 'utf8');

  assert.match(vite, /optimizeDeps/);
  assert.match(vite, /entries:\s*\['index\.html'\]/);
  assert.match(vite, /watch:\s*\{/);
  assert.match(vite, /\*\*\/resources\/\*\*/);
  assert.match(vite, /\*\*\/release\/\*\*/);
  assert.match(vite, /\*\*\/data\/\*\*/);
  assert.match(vite, /rollupOptions/);
  assert.match(vite, /input:\s*'index\.html'/);
  assert.match(buildScript, /\$venvRoot/);
  assert.match(buildScript, /python\.exe/);
  assert.match(buildScript, /requirements-build\.txt/);
  assert.match(buildScript, /backend-dist size/);
  assert.match(buildScript, /METIS_BACKEND_DIST_MAX_MB/);
  assert.match(buildScript, /Invoke-WebRequest[\s\S]*\/health/);
  assert.match(spec, /"jupyterlab"/);
  assert.match(spec, /"notebook"/);
  assert.match(spec, /"bokeh"/);
  assert.match(spec, /"distributed"/);
  assert.match(spec, /"sphinx"/);
  assert.match(requirementsBuild, /PyInstaller/);
  assert.match(requirementsBuild, /tree-sitter/);
  assert.match(gitignore, /desktop\/data\//);
  assert.match(gitignore, /desktop\/resources\/backend-dist\//);
});

test('FABLEADV-11 preview and tool card fixes stay wired', () => {
  const security = read('electron/security.cjs');
  const main = read('electron/main.cjs');
  const preload = read('electron/preload.cjs');
  const globals = read('src/global.d.ts');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const css = read('src/index.css');
  const workspaceRoutes = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'workspace_routes.py'), 'utf8');
  const flaskSmoke = fs.readFileSync(path.resolve(root, '..', 'backend', 'tests', 'test_flask_runtime_sse_smoke.py'), 'utf8');
  const diffPreview = read('src/lib/diffPreview.ts');
  const diffPreviewTest = read('src/lib/__tests__/diffPreview.test.ts');
  const uiStore = read('src/store/uiStore.ts');
  const toolCallBlock = read('src/components/chat/ToolCallBlock.tsx');
  const chatStore = read('src/store/chatStore.ts');
  const smoke = read('src/runtime/rendererSmoke.ts');

  assert.match(security, /sandbox:\s*true/);
  assert.match(security, /webviewTag:\s*false/);
  assert.match(main, /WebContentsView/);
  assert.match(main, /PREVIEW_WEB_PREFERENCES/);
  assert.match(main, /routePreviewWindowOpen/);
  assert.match(main, /metis:preview-set-bounds/);
  assert.match(main, /metis:preview-load/);
  assert.match(main, /metis:preview-state/);
  assert.match(preload, /previewSetBounds/);
  assert.match(preload, /previewLoad/);
  assert.match(preload, /onPreviewState/);
  assert.match(globals, /previewSetBounds/);
  assert.match(globals, /onPreviewState/);
  assert.match(rightRail, /web-preview-host/);
  assert.match(rightRail, /previewSetBounds/);
  assert.match(rightRail, /previewLoad/);
  assert.match(rightRail, /onPreviewState/);
  assert.match(css, /\.web-preview-host/);
  assert.match(workspaceRoutes, /\/file-preview-root\/<token>\/<path:relative_path>/);
  assert.match(workspaceRoutes, /_inject_file_preview_base/);
  assert.match(flaskSmoke, /serves_relative_assets_with_token_root/);
  assert.doesNotMatch(diffPreview, /未知文件/);
  assert.match(diffPreview, /if \(!displayPath\) return null/);
  assert.match(diffPreviewTest, /do not include a path/);
  assert.match(uiStore, /expandedToolCards/);
  assert.match(uiStore, /setToolCardExpanded/);
  assert.match(toolCallBlock, /stableToolCardId/);
  assert.match(toolCallBlock, /setToolCardExpanded/);
  assert.match(chatStore, /clearExpandedToolCards/);
  assert.match(smoke, /new73-preview-view-ipc-enabled/);
  assert.match(smoke, /new107-preview-view-main-process-hosted/);
  assert.doesNotMatch(main, /did-attach-webview|metis:webview-open-url|captureWebviewPopupWindow|isBlankPopupUrl/);
  assert.doesNotMatch(preload, /onWebviewOpenUrl|metis:webview-open-url/);
  assert.doesNotMatch(globals, /onWebviewOpenUrl/);
  assert.doesNotMatch(rightRail, /WEBVIEW_IN_PLACE_LINK_SCRIPT|createElement\(['"]webview['"]\)|onWebviewOpenUrl/);
});

test('electron window keeps hardened renderer defaults', () => {
  const main = read('electron/main.cjs');
  const security = read('electron/security.cjs');
  assert.match(main, /HARDENED_WEB_PREFERENCES/);
  assert.match(security, /contextIsolation:\s*true/);
  assert.match(security, /nodeIntegration:\s*false/);
  assert.match(security, /sandbox:\s*true/);
  assert.match(security, /webSecurity:\s*true/);
  // P0：swiftshader 软件渲染在沙箱下崩溃(黑屏)，打包版必须关闭沙箱。锁死此修复以防回归。
  assert.match(main, /appendSwitch\(['"]no-sandbox['"]\)/);
  assert.match(main, /appendSwitch\(['"]disable-gpu-sandbox['"]\)/);
  assert.match(main, /setWindowOpenHandler/);
  assert.match(main, /will-navigate/);
});

test('preload exposes only the metis bridge', () => {
  const preload = read('electron/preload.cjs');
  assert.match(preload, /contextBridge\.exposeInMainWorld\('metis'/);
  assert.doesNotMatch(preload, /require\(['"]fs['"]\)/);
  assert.doesNotMatch(preload, /process\.env/);
});

test('desktop performance harness remains wired', () => {
  const main = read('src/main.tsx');
  const preload = read('electron/preload.cjs');
  const electronMain = read('electron/main.cjs');
  const perfRunner = read('scripts/desktop-perf-runner.mjs');
  const rendererPerf = read('src/runtime/rendererPerf.ts');
  const perfBudgets = read('src/runtime/perfBudgets.ts');
  const ledgerPath = path.resolve(root, '..', 'docs', 'dev-log', 'PERFORMANCE-LEDGER.md');
  const ledger = fs.readFileSync(ledgerPath, 'utf8');
  assert.match(main, /metisPerf/);
  assert.match(main, /rendererPerf/);
  assert.match(preload, /reportPerfResult/);
  assert.match(electronMain, /METIS_PERF_RESULT/);
  assert.match(electronMain, /budgets/);
  assert.match(electronMain, /METIS_DESKTOP_PERF/);
  assert.match(perfRunner, /METIS_PERF_RESULT/);
  assert.match(rendererPerf, /evaluateDesktopPerfBudgets/);
  assert.match(rendererPerf, /measureLongThread/);
  assert.match(rendererPerf, /measurePanelMotion/);
  assert.match(rendererPerf, /measureMarkdownHeavy/);
  assert.match(rendererPerf, /measureToolOutputHeavy/);
  assert.match(rendererPerf, /measureRightRailToolPreview/);
  assert.match(rendererPerf, /TRANSCRIPT_REPLAY_FIXTURE/);
  assert.match(rendererPerf, /measureTranscriptReplay/);
  assert.match(perfBudgets, /DESKTOP_PERF_BUDGETS/);
  assert.match(perfBudgets, /longThreadInitialRowsMax:\s*90/);
  assert.match(perfBudgets, /markdownHeavyRenderMsMax/);
  assert.match(perfBudgets, /toolCardExpandMsMax/);
  assert.match(perfBudgets, /rightRailToolPreviewMsMax/);
  assert.match(perfBudgets, /transcriptReplayTotalMsMax/);
  assert.match(perfBudgets, /transcriptReplayP95FrameMsMax/);
  assert.match(perfBudgets, /transcriptReplayRightRailPreviewMsMax/);
  assert.match(ledger, /NEW-48 Baseline/);
  assert.match(ledger, /NEW-49 Markdown And Tool Output Baseline/);
  assert.match(ledger, /NEW-50 Transcript Replay Baseline/);
  assert.match(ledger, /initial mounted rows/);
  assert.match(ledger, /Markdown heavy/);
  assert.match(ledger, /Right rail tool preview/);
  assert.match(ledger, /Transcript replay/);
});

test('right rail web preview supports multiple closeable tabs', () => {
  const uiStore = read('src/store/uiStore.ts');
  const titlebar = read('src/components/shell/Titlebar.tsx');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const css = read('src/index.css');

  assert.match(uiStore, /interface WebPreviewTab/);
  assert.match(uiStore, /webPreviewTabs:\s*WebPreviewTab\[\]/);
  assert.match(uiStore, /activeWebPreviewId:\s*string/);
  assert.match(uiStore, /activateWebPreviewTab/);
  assert.match(uiStore, /closeWebPreviewTab/);
  assert.match(uiStore, /\.slice\(-8\)/);
  assert.match(rightRail, /web-tab-strip/);
  assert.match(titlebar, /workspace-card-menu/);
  assert.doesNotMatch(rightRail, /workspace-card-menu-button/);
  assert.match(titlebar, /titlebar-cards-menu-button/);
  assert.match(rightRail, /WebPreviewTabButton/);
  assert.match(rightRail, /className="web-tab-close"/);
  assert.match(rightRail, /role="tablist"/);
  assert.match(rightRail, /key:\s*tab\.id/);
  assert.match(smoke, /right-rail-web-tabs-multiple/);
  assert.match(smoke, /right-rail-web-tab-close-removes-tab/);
  assert.match(smoke, /right-rail-card-menu-compact/);
  assert.match(css, /\.right-rail-inner\.workspace-card-shell[\s\S]*grid-template-rows:\s*minmax\(0,\s*1fr\)/);
  assert.match(css, /\.workspace-card-menu[\s\S]*position:\s*absolute/);
  assert.match(css, /\.right-rail-tabs/);
  assert.match(css, /\.web-tab-strip/);
  assert.match(css, /\.web-preview-tab/);
  assert.match(css, /\.web-tab-close/);
});

test('right rail browser workbench keeps navigation and zoom wired', () => {
  const uiStore = read('src/store/uiStore.ts');
  const main = read('electron/main.cjs');
  const preload = read('electron/preload.cjs');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const css = read('src/index.css');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-51-Right-Rail-Browser-Workbench.md'),
    'utf8',
  );

  assert.match(uiStore, /zoom:\s*number/);
  assert.match(uiStore, /loading:\s*boolean/);
  assert.match(uiStore, /error:\s*string/);
  assert.match(uiStore, /updateWebPreviewTab/);
  assert.match(uiStore, /setWebPreviewZoom/);
  assert.match(uiStore, /normalizeWebZoom/);
  assert.match(rightRail, /web-browser-toolbar/);
  assert.match(rightRail, /web-back-button/);
  assert.match(rightRail, /web-forward-button/);
  assert.match(rightRail, /web-reload-button/);
  assert.match(rightRail, /web-more-button/);
  assert.match(rightRail, /web-more-menu/);
  assert.match(rightRail, /缩小页面/);
  assert.match(rightRail, /放大页面/);
  assert.match(rightRail, /恢复 100%/);
  assert.match(rightRail, /系统浏览器打开/);
  assert.doesNotMatch(rightRail, /web-open-button/);
  assert.match(rightRail, /previewSetZoom/);
  assert.match(rightRail, /webCardVisible/);
  assert.match(rightRail, /schedulePreviewBoundsSync/);
  assert.match(rightRail, /previewSetZoom\?\.\(activeWebZoom\)[\s\S]*schedulePreviewBoundsSync/);
  assert.match(rightRail, /cardId === 'web'[\s\S]*hidePreviewView\(\)/);
  assert.match(rightRail, /!rightRailOpen \|\| !webCardVisible[\s\S]*hidePreviewView\(\)/);
  assert.match(main, /setZoomFactor/);
  assert.match(main, /tabId !== previewTabId/);
  assert.match(main, /bounds\.width <= 4 \|\| bounds\.height <= 4/);
  assert.match(preload, /previewSetZoom/);
  assert.match(rightRail, /openExternal/);
  assert.match(rightRail, /reloadActiveWeb/);
  assert.match(main, /did-start-loading/);
  assert.match(main, /did-fail-load/);
  assert.match(rightRail, /onPreviewState/);
  assert.match(smoke, /right-rail-web-zoom-in-updates-tab/);
  assert.match(smoke, /right-rail-web-toolbar-visible/);
  assert.match(css, /\.web-browser-toolbar/);
  assert.match(css, /\.web-toolbar-button/);
  assert.match(css, /\.web-more-menu/);
  assert.match(doc, /NEW-51/);
});

test('FABLEADV-50 preview browser automation and safety gate stay wired', () => {
  const main = read('electron/main.cjs');
  const preload = read('electron/preload.cjs');
  const globals = read('src/global.d.ts');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const toolCard = read('src/components/chat/ToolCallBlock.tsx');
  const types = read('src/lib/types.ts');
  const css = read('src/index.css');
  const bridge = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'preview_bridge.py'), 'utf8');
  const toolRegistry = fs.readFileSync(path.resolve(root, '..', 'backend', 'runtime', 'tool_registry.py'), 'utf8');
  const toolProfiles = fs.readFileSync(path.resolve(root, '..', 'backend', 'runtime', 'tool_profiles.py'), 'utf8');
  const skillLoader = fs.readFileSync(path.resolve(root, '..', 'backend', 'runtime', 'skill_loader.py'), 'utf8');
  const browserSkill = fs.readFileSync(
    path.resolve(root, '..', 'backend', 'resources', 'builtin_skills', 'browser', 'SKILL.md'),
    'utf8',
  );
  const tests = fs.readFileSync(path.resolve(root, '..', 'backend', 'tests', 'test_preview_browser_bridge.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'FABLEADV-50-Preview-Browser-MVP.md'),
    'utf8',
  );

  assert.match(main, /function observePreviewPage/);
  assert.match(main, /function performPreviewAction/);
  assert.match(main, /PREVIEW_LOCAL_PORT_CANDIDATES = \[5173,\s*5174,\s*3000,\s*4200,\s*8000,\s*8080\]/);
  assert.match(main, /function resolvePreviewNavigationUrl/);
  assert.match(main, /METIS_DESKTOP_DEV_SERVER/);
  assert.match(main, /function isPreviewCurrentAlias/);
  assert.match(main, /current-preview-url/);
  assert.match(main, /fallback-dead-local-port/);
  assert.match(main, /function previewDiagnosticsPayload/);
  assert.match(main, /function buildPreviewPageHealth/);
  assert.match(main, /function installPreviewPageDiagnosticsHooks/);
  assert.match(main, /webContents\.on\('console-message',\s*recordPreviewConsoleMessage\)/);
  assert.match(main, /webRequest\.onErrorOccurred/);
  assert.match(main, /window\.addEventListener\('unhandledrejection'/);
  assert.match(main, /dom_summary/);
  assert.match(main, /page_health/);
  assert.match(main, /analyzePreviewImageHealth/);
  assert.match(main, /screenshot_health/);
  assert.match(main, /async function loadPreviewUrl/);
  assert.match(main, /await view\.webContents\.loadURL\(value\)/);
  assert.match(main, /previewLoadedUrls\.delete\(previewTabId\)/);
  assert.match(main, /PREVIEW_RISK_PATTERN/);
  assert.match(main, /confirmPreviewRisk/);
  assert.match(main, /dialog\.showMessageBox/);
  assert.match(main, /recordPreviewAction/);
  assert.match(main, /function previewActivityLabel/);
  assert.match(main, /function previewActivityPayload/);
  assert.match(main, /kind === 'activity'/);
  assert.match(main, /metis:preview-observe/);
  assert.match(main, /metis:preview-action/);
  assert.match(main, /metis:preview-activity/);
  assert.match(main, /browser_activity/);
  assert.match(preload, /previewObserve/);
  assert.match(preload, /previewAction/);
  assert.match(preload, /previewActivity/);
  assert.match(globals, /previewObserve/);
  assert.match(globals, /previewAction/);
  assert.match(globals, /previewActivity/);
  assert.match(globals, /BrowserActivityPayload/);
  assert.match(types, /interface BrowserActivityItem/);
  assert.match(types, /interface BrowserActivityPayload/);
  assert.match(rightRail, /BrowserActivityPanel/);
  assert.match(rightRail, /previewActivity\(\{ limit: 24 \}\)/);
  assert.match(rightRail, /browser-activity-panel/);
  assert.match(toolCard, /browserActivitySummaryFromResult/);
  assert.match(toolCard, /tool-browser-activity-summary/);
  assert.match(css, /\.browser-activity-panel/);
  assert.match(css, /\.tool-browser-activity-summary/);
  assert.match(bridge, /preview_bridge_bp/);
  assert.match(bridge, /\/api\/preview-browser\/next/);
  assert.match(bridge, /\/api\/preview-browser\/result/);
  assert.match(toolRegistry, /preview_browser_observe/);
  assert.match(toolRegistry, /preview_browser_action/);
  assert.match(toolRegistry, /bare localhost\/current page requests/);
  assert.match(toolRegistry, /console warnings\/errors/);
  assert.match(toolRegistry, /failed network\s+requests/);
  assert.match(toolRegistry, /button visibility\/clickability/);
  assert.match(toolRegistry, /require_screenshot_not_blank/);
  assert.match(toolRegistry, /确认登录按钮可见并可点击/);
  assert.match(toolRegistry, /hard-blocks/);
  assert.match(toolRegistry, /_compact_preview_browser_activity/);
  assert.match(toolRegistry, /browser_activity/);
  assert.match(toolProfiles, /preview_browser_observe/);
  assert.match(toolProfiles, /browse_and_extract/);
  const builtinSkillsVersion = Number(skillLoader.match(/BUILTIN_SKILLS_VERSION = (\d+)/)?.[1] || 0);
  assert.ok(builtinSkillsVersion >= 6);
  assert.match(skillLoader, /Allowed tools:/);
  assert.match(browserSkill, /Browser Router/);
  assert.match(browserSkill, /preview_browser_observe/);
  assert.match(browserSkill, /preview_browser_action/);
  assert.match(browserSkill, /localhost:5173/);
  assert.match(browserSkill, /diagnostics/);
  assert.match(browserSkill, /page_health/);
  assert.match(browserSkill, /一句话验收/);
  assert.match(browserSkill, /screenshot_health/);
  assert.match(browserSkill, /browse_and_extract/);
  assert.match(tests, /test_preview_bridge_round_trips_command_result/);
  assert.match(tests, /test_preview_browser_verify_supports_browser_verifier/);
  assert.match(doc, /Phase 2/);
  assert.match(doc, /Phase 3/);
  assert.match(doc, /Phase 5/);
  assert.match(doc, /Phase 7/);
  assert.match(doc, /Phase 8/);
  assert.match(doc, /Phase 9/);
  assert.match(doc, /高风险动作确认门禁/);
});

test('NEW-82 agent activity and compact tool calls stay wired', () => {
  const uiStore = read('src/store/uiStore.ts');
  const thread = read('src/components/chat/MetisThread.tsx');
  const subagentGroup = read('src/components/chat/SubagentGroup.tsx');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const css = read('src/index.css');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-82-Agent-Activity-And-Tool-Call-Polish.md'),
    'utf8',
  );

  assert.match(uiStore, /'activity'/);
  assert.match(thread, /hiddenSubagentSignature/);
  assert.match(thread, /showSubagentStrip/);
  assert.match(subagentGroup, /SubagentActivityPanel/);
  assert.match(subagentGroup, /subagent-dismiss-button/);
  assert.match(subagentGroup, /subagent-open-activity-button/);
  assert.match(rightRail, /renderActivityPanel/);
  assert.match(rightRail, /cards:\s*\['activity', 'plan'\]/);
  assert.match(rightRail, /activity-inline-tool-output/);
  assert.match(rightRail, /SubagentActivityPanel/);
  assert.match(css, /\.activity-pane/);
  assert.match(css, /\.subagent-strip-main/);
  assert.match(css, /\.subagent-activity-panel/);
  assert.match(css, /width:\s*min\(var\(--chat-column-width\),\s*100%\)/);
  assert.match(css, /\.tool-card-actions/);
  assert.match(smoke, /new82-subagent-strip-dismisses/);
  assert.match(smoke, /new82-tool-card-compact-width/);
  assert.match(doc, /NEW-82/);
});

test('file change diff workbench and custom OpenAI relay stay wired', () => {
  const uiStore = read('src/store/uiStore.ts');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const messageBubble = read('src/components/chat/MessageBubble.tsx');
  const fileChangeReview = read('src/components/chat/FileChangeReviewCard.tsx');
  const diffPreview = read('src/lib/diffPreview.ts');
  const api = read('src/lib/api.ts');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const settings = readSettingsSources();
  const setup = read('src/components/setup/SetupWizard.tsx');
  const fakeBackend = read('electron/backend.cjs');
  const realProfiles = fs.readFileSync(
    path.resolve(root, '..', 'backend', 'bridges', 'provider_profiles.py'),
    'utf8',
  );
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-52-File-Changes-Diff-And-Custom-OpenAI.md'),
    'utf8',
  );

  assert.match(diffPreview, /buildFileChangePreview/);
  assert.match(diffPreview, /write_file/);
  assert.match(diffPreview, /edit_file/);
  assert.match(diffPreview, /delete_file/);
  assert.match(uiStore, /rightRailMode:\s*'diff'/);
  assert.match(uiStore, /diffPreview:\s*FileChangePreview\s*\|\s*null/);
  assert.match(uiStore, /setDiffPreview/);
  assert.match(rightRail, /diff-preview-pane/);
  assert.match(rightRail, /diff-table/);
  assert.match(rightRail, /文件变更/);
  assert.match(messageBubble, /FileChangeReviewCard/);
  assert.match(fileChangeReview, /buildFileChangePreview/);
  assert.match(fileChangeReview, /file-change-review-card/);
  assert.match(css, /\.diff-preview-pane/);
  assert.match(css, /\.diff-line/);
  assert.match(smoke, /right-rail-diff-auto-opens-file-change/);
  assert.match(smoke, /provider-custom-openai-local-verify-no-network/);
  assert.match(settings, /custom-openai/);
  assert.match(setup, /custom-openai/);
  assert.match(setup, /apiKeyFormatHint/);
  assert.match(setup, /setApiKey\(value => value\.trim\(\)\)/);
  assert.match(setup, /setup-verification/);
  assert.match(api, /export async function verifyFirstRun/);
  assert.match(api, /return providerValidationFromRecord\(data\)/);
  assert.match(css, /\.setup-verification/);
  assert.match(fakeBackend, /provider_id:\s*'custom-openai'/);
  assert.match(fakeBackend, /display_name:\s*'自定义 OpenAI 中转站'/);
  assert.match(realProfiles, /ProviderId\("custom-openai"\)/);
  assert.match(realProfiles, /自定义 OpenAI 中转站/);
  assert.match(doc, /NEW-52/);
});

test('NEW-52.5 developer workflow polish stays wired', () => {
  const types = read('src/lib/types.ts');
  const api = read('src/lib/api.ts');
  const settings = readSettingsSources();
  const uiStore = read('src/store/uiStore.ts');
  const theme = read('src/hooks/useTheme.ts');
  const terminal = read('src/components/terminal/TerminalPanel.tsx');
  const statusbar = read('src/components/shell/Statusbar.tsx');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const preload = read('electron/preload.cjs');
  const electronMain = read('electron/main.cjs');
  const chatStore = read('src/store/chatStore.ts');
  const sseParser = read('src/store/sseParser.ts');
  const webPreview = read('src/lib/webPreview.ts');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const css = read('src/index.css');
  const parityPath = path.resolve(root, 'src/lib/hermesParity.ts');
  const llmState = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'llm_state.py'), 'utf8');
  const llmCommon = fs.readFileSync(path.resolve(root, '..', 'backend', 'runtime', 'llm_backends', '_common.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-52.5-Real-Developer-Workflow-Polish.md'),
    'utf8',
  );

  assert.match(types, /ProxyMode = 'system' \| 'custom' \| 'off'/);
  assert.match(types, /TerminalRunResult/);
  assert.match(api, /proxy_mode/);
  assert.match(api, /terminal_shell/);
  assert.match(settings, /代理设置/);
  assert.match(settings, /默认终端/);
  assert.doesNotMatch(settings, /Hermes 靠齐度|parity-panel|fake-backend-warning/);
  assert.match(uiStore, /fontFamily/);
  assert.match(uiStore, /terminalOpen/);
  assert.match(theme, /fontStacks/);
  assert.match(terminal, /terminalCreate/);
  assert.doesNotMatch(statusbar, /status-chat-launcher|setTerminalOpen|setRightRailOpen/);
  assert.match(rightRail, /TerminalPanel embedded/);
  assert.match(preload, /terminalRun/);
  assert.match(electronMain, /metis:terminal-run/);
  assert.match(electronMain, /fakeBackend/);
  assert.match(sseParser, /findSafeLocalPreviewUrl/);
  assert.match(webPreview, /findSafeLocalPreviewUrl/);
  assert.match(webPreview, /localhost|127/);
  assert.equal(fs.existsSync(parityPath), false);
  assert.match(llmState, /proxy_mode/);
  assert.match(llmState, /METIS_LLM_PROXY/);
  assert.match(llmCommon, /METIS_PROXY_MODE/);
  assert.match(css, /\.terminal-panel/);
  assert.match(css, /\.markdown-body table/);
  assert.match(smoke, /new52-5-terminal-run-powershell/);
  assert.match(smoke, /new52-5-local-preview-auto-opens/);
  assert.match(doc, /NEW-52\.5/);
});

test('NEW-83 appearance font size controls stay wired', () => {
  const uiStore = read('src/store/uiStore.ts');
  const theme = read('src/hooks/useTheme.ts');
  const settings = readSettingsSources();
  const smoke = read('src/runtime/rendererSmoke.ts');
  const css = read('src/index.css');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-83-Appearance-Font-Size-Controls.md'),
    'utf8',
  );

  assert.match(uiStore, /uiFontSize/);
  assert.match(uiStore, /codeFontSize/);
  assert.match(uiStore, /metis\.uiFontSize/);
  assert.match(uiStore, /metis\.codeFontSize/);
  assert.match(theme, /--ui-font-size/);
  assert.match(theme, /--code-font-size/);
  assert.match(settings, /FontSizeControl/);
  assert.match(settings, /UI 字号/);
  assert.match(settings, /代码字号/);
  assert.match(css, /--ui-font-size:\s*14px/);
  assert.match(css, /--code-font-size:\s*12px/);
  assert.match(css, /\.settings-size-row/);
  assert.match(css, /\.message-bubble,[\s\S]*\.markdown-body,[\s\S]*\.composer textarea/);
  assert.match(smoke, /new83-ui-font-size-applies/);
  assert.match(smoke, /new83-code-font-size-applies/);
  assert.match(doc, /NEW-83/);
});

test('NEW-84 session isolation and shell profiles stay wired', () => {
  const chatStore = read('src/store/chatStore.ts');
  const sseParser = read('src/store/sseParser.ts');
  const types = read('src/lib/types.ts');
  const api = read('src/lib/api.ts');
  const main = read('electron/main.cjs');
  const terminal = read('src/components/terminal/TerminalPanel.tsx');
  const settings = readSettingsSources();
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-84-Session-Isolation-And-Shell-Profiles.md'),
    'utf8',
  );

  assert.match(chatStore, /runSessionId:\s*string\s*\|\s*null/);
  assert.match(chatStore, /chatStream\(\{ message: userContent, session_id: sessionId \}/);
  assert.match(sseParser, /export function applyChatEvent/);
  assert.match(sseParser, /assistantId,\s*sessionId/);
  assert.match(sseParser, /export function isActiveSession/);
  assert.match(chatStore, /persistBackgroundRunSnapshot/);
  assert.match(sseParser, /pendingAssistantText = new Map<string, \{ sessionId: string \| null; text: string \}>/);
  assert.match(types, /TerminalShell[\s\S]*'bash'[\s\S]*'sh'[\s\S]*'shell'/);
  assert.match(api, /function terminalShellValue/);
  assert.match(main, /normalizeTerminalShell/);
  assert.match(main, /shellId === 'bash'/);
  assert.match(main, /shellId === 'sh'/);
  assert.match(main, /shellId === 'shell'/);
  assert.match(main, /posixTerminalProfile/);
  assert.match(terminal, /value: 'bash'/);
  assert.match(terminal, /value: 'sh'/);
  assert.match(terminal, /value: 'shell'/);
  assert.match(settings, /terminalShellOptions/);
  assert.match(settings, /value: 'bash'/);
  assert.match(settings, /value: 'sh'/);
  assert.match(settings, /value: 'shell'/);
  assert.match(smoke, /new84-terminal-settings-shell-options/);
  assert.match(smoke, /new100-terminal-menu-no-default-shell/);
  assert.match(doc, /Session Isolation And Shell Profiles/);
  assert.match(doc, /true multi-session background execution needs a later backend task queue/);
});

test('NEW-85 real provider and backend session routing stay wired', () => {
  const realBackend = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'app.py'), 'utf8');
  const flaskSmoke = fs.readFileSync(path.resolve(root, '..', 'backend', 'tests', 'test_flask_runtime_sse_smoke.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-85-Real-Provider-And-Session-Stability.md'),
    'utf8',
  );

  assert.match(realBackend, /def _request_session_id/);
  assert.match(realBackend, /data\.get\("session_id"\)/);
  assert.match(realBackend, /def _prepare_chat_session/);
  assert.match(realBackend, /def _save_session_history/);
  assert.match(realBackend, /run_history = list\(history if history is not None else messages\)/);
  assert.match(realBackend, /model_context = _model_context_with_skill_invocation\(history, compact_state, workspace_root\)/);
  assert.match(realBackend, /_stream_agent_response\([\s\S]*model_context[\s\S]*session_id=session_id[\s\S]*history=history[\s\S]*compact_state=compact_state[\s\S]*mode=mode[\s\S]*checkpoint=checkpoint/);
  assert.match(flaskSmoke, /test_chat_sse_honors_request_session_id_without_polluting_active_session/);
  assert.match(flaskSmoke, /session_id=target\.id/);
  assert.match(doc, /Real Provider And Session Stability/);
  assert.match(doc, /Do not persist the API key/);
  assert.match(doc, /True concurrent background agents/);
});

test('NEW-86 industrial run registry and concurrent sessions stay wired', () => {
  const realBackend = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'app.py'), 'utf8');
  const flaskSmoke = fs.readFileSync(path.resolve(root, '..', 'backend', 'tests', 'test_flask_runtime_sse_smoke.py'), 'utf8');
  const types = read('src/lib/types.ts');
  const api = read('src/lib/api.ts');
  const chatStore = read('src/store/chatStore.ts');
  const runManager = read('src/store/runManager.ts');
  const fakeBackend = read('electron/backend.cjs');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-86-Industrial-Run-Registry-And-Concurrent-Sessions.md'),
    'utf8',
  );

  assert.match(realBackend, /_RUN_ACTIVE_STATES/);
  assert.match(realBackend, /def _create_run_state/);
  assert.match(realBackend, /def _run_registry_worker/);
  assert.match(realBackend, /@app\.route\("\/runs", methods=\["POST"\]\)/);
  assert.match(realBackend, /@app\.route\("\/runs\/<run_id>\/events"/);
  assert.match(realBackend, /@app\.route\("\/sessions\/<session_id>\/runs\/active"/);
  assert.match(realBackend, /session already has an active run/);
  assert.match(flaskSmoke, /test_run_registry_streams_replayable_events_to_target_session/);
  assert.match(flaskSmoke, /test_run_registry_cancel_endpoint_marks_active_run_canceling/);
  assert.match(flaskSmoke, /test_run_registry_rejects_second_active_run_for_same_session/);
  assert.match(types, /interface ChatRunPayload/);
  assert.match(types, /interface ActiveChatRunPayload/);
  assert.match(types, /run_id\?: string/);
  assert.match(api, /export async function startChatRun/);
  assert.match(api, /export async function runEventStream/);
  assert.match(api, /export async function cancelChatRun/);
  assert.match(api, /export async function getActiveSessionRun/);
  assert.match(runManager, /const activeRunControllers = new Map/);
  assert.match(runManager, /export const processedRunSeq = new Map/);
  assert.match(chatStore, /startChatRun\(\{ message: userContent, session_id: sessionId, assistant_id: assistantId \}\)/);
  assert.match(chatStore, /attachRunStream\(activeRunInfo, sessionId\)/);
  assert.match(chatStore, /cancelChatRun\(activeRun\.runId\)/);
  assert.match(fakeBackend, /const fakeRuns = new Map/);
  assert.match(fakeBackend, /function fakeCreateRun/);
  assert.match(fakeBackend, /async function handleFakeRunEvents/);
  assert.match(fakeBackend, /pathname === '\/runs'/);
  assert.match(fakeBackend, /pathname\.endsWith\('\/runs\/active'\)/);
  assert.match(doc, /NEW-86 Industrial Run Registry/);
  assert.match(doc, /cooperative cancellation/);
  assert.match(doc, /不继续 NEW-87/);
});

test('NEW-87 abortable provider and tool isolation stays wired', () => {
  const realBackend = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'app.py'), 'utf8');
  const agentLoop = fs.readFileSync(path.resolve(root, '..', 'backend', 'runtime', 'agent_loop.py'), 'utf8');
  const cancellation = fs.readFileSync(path.resolve(root, '..', 'backend', 'runtime', 'cancellation.py'), 'utf8');
  const llmCommon = fs.readFileSync(path.resolve(root, '..', 'backend', 'runtime', 'llm_backends', '_common.py'), 'utf8');
  const openaiCompat = fs.readFileSync(path.resolve(root, '..', 'backend', 'runtime', 'llm_backends', 'openai_compat.py'), 'utf8');
  const toolRegistry = fs.readFileSync(path.resolve(root, '..', 'backend', 'runtime', 'tool_registry.py'), 'utf8');
  const shellTool = fs.readFileSync(
    path.resolve(root, '..', 'backend', 'tools', 'coding', 'foundation', 'cli', 'execute_shell.py'),
    'utf8',
  );
  const flaskSmoke = fs.readFileSync(path.resolve(root, '..', 'backend', 'tests', 'test_flask_runtime_sse_smoke.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-87-Abortable-Provider-And-Tool-Isolation.md'),
    'utf8',
  );

  assert.match(cancellation, /class OperationCancelled/);
  assert.match(cancellation, /def cancellation_context/);
  assert.match(realBackend, /"cancel_event": threading\.Event\(\)/);
  assert.match(realBackend, /cancel_event\.set\(\)/);
  assert.match(realBackend, /run_stream\(messages, config, registry=registry, cancel_event=cancel_event\)/);
  assert.match(agentLoop, /cancel_event: Optional\[threading\.Event\]/);
  assert.match(agentLoop, /backend\.chat_stream/);
  assert.match(agentLoop, /stream_kwargs\["cancel_event"\]/);
  assert.match(agentLoop, /daemon=True/);
  assert.match(agentLoop, /Tool execution cancelled|Tool execution canceled/);
  assert.match(llmCommon, /def post_with_retries/);
  assert.match(llmCommon, /cancel_event: Optional\[threading\.Event\]/);
  assert.match(llmCommon, /session\.close\(\)/);
  assert.match(llmCommon, /response\.close\(\)/);
  assert.match(openaiCompat, /iter_utf8_lines\(response, cancel_event=cancel_event\)/);
  assert.match(toolRegistry, /except OperationCancelled/);
  assert.match(shellTool, /def _kill_process_tree/);
  assert.match(shellTool, /taskkill/);
  assert.match(shellTool, /current_cancel_event/);
  assert.match(flaskSmoke, /test_run_registry_cancel_aborts_blocking_provider_stream/);
  assert.match(flaskSmoke, /test_run_registry_cancel_releases_blocking_tool_execution/);
  assert.match(doc, /NEW-87 Abortable Provider And Tool Isolation/);
  assert.match(doc, /不继续 NEW-88/);
});

test('NEW-91 background run activity center stays wired', () => {
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const api = read('src/lib/api.ts');
  const types = read('src/lib/types.ts');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-91-Background-Run-Activity-Center.md'),
    'utf8',
  );

  assert.match(api, /export async function getChatRuns/);
  assert.match(api, /`\/runs\$\{query\}`/);
  assert.match(types, /interface ChatRunsPayload/);
  assert.match(rightRail, /function RunActivityCenter/);
  assert.match(rightRail, /getChatRuns\(\)/);
  assert.match(rightRail, /cancelChatRun\(run\.runId\)/);
  // FABLEADV-28：运行卡跳转由 setToolPreview 改为行点击 selectSession（下一行即覆盖）。
  assert.match(rightRail, /selectSession\(run\.sessionId\)/);
  assert.match(css, /\.run-activity-center/);
  assert.match(css, /\.run-activity-card/);
  assert.match(smoke, /new91-run-activity-center-visible/);
  assert.match(smoke, /new91-run-cancel-requests-canceling/);
  assert.match(doc, /NEW-91 Background Run Activity Center/);
  assert.match(doc, /GET \/runs/);
  assert.match(doc, /停止条件/);
});

test('NEW-92 Hermes parity stability and UI elegance pass stays wired', () => {
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const css = read('src/index.css');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-92-Hermes-Parity-Stability-And-UI-Elegance-Pass.md'),
    'utf8',
  );

  // FABLEADV-28：稳定性总览(StabilitySnapshot)按用户要求移除，改为运行卡上的状态色点(run-status-dot)。
  assert.match(rightRail, /function RunActivityCenter/);
  assert.match(rightRail, /run-status-dot/);
  assert.match(rightRail, /data-tone=\{dotTone\}/);
  assert.match(css, /\.run-status-dot/);
  assert.match(css, /\.run-status-dot\[data-tone='running'\]/);
  assert.match(doc, /Hermes 的强项/);
  assert.match(doc, /boot-failure-overlay\.tsx/);
  assert.match(doc, /停止条件/);
});

test('NEW-93 busy session guard and sidebar status language stays wired', () => {
  const chatStore = read('src/store/chatStore.ts');
  const sidebar = read('src/components/sidebar/Sidebar.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-93-Busy-Session-Guard-And-Sidebar-Status-Language.md'),
    'utf8',
  );

  assert.match(chatStore, /phase:\s*'session_busy'/);
  assert.match(chatStore, /当前会话正在运行/);
  assert.match(sidebar, /session-state-dot/);
  assert.match(sidebar, /data-status={runStatus \|\| 'idle'}/);
  assert.match(css, /\.session-state-dot/);
  assert.match(css, /@keyframes session-state-pulse/);
  assert.match(smoke, /new93-busy-send-guard-visible/);
  assert.match(smoke, /new93-session-state-dot-visible/);
  assert.match(doc, /Hermes 对比结论/);
  assert.match(doc, /停止条件/);
});

test('NEW-94 tool activity tree and command center stays wired', () => {
  const thread = [
    read('src/components/chat/MessageBubble.tsx'),
    read('src/components/chat/ToolCallBlock.tsx'),
  ].join('\n');
  const commandCenter = read('src/components/command/CommandPalette.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-94-Tool-Activity-Tree-And-Command-Center.md'),
    'utf8',
  );

  assert.match(thread, /ToolGroup:\s*ToolActivityGroup/);
  assert.match(thread, /tool-activity-group/);
  assert.match(thread, /toolCommandPreview/);
  assert.match(thread, /tool-card-open/);
  assert.match(thread, /tool-card-diff/);
  assert.match(commandCenter, /type CommandCenterTab = 'search' \| 'system' \| 'runs' \| 'provider'/);
  assert.match(commandCenter, /getProviderStatus/);
  assert.match(commandCenter, /getChatRuns/);
  assert.match(commandCenter, /getDeskStatus/);
  assert.match(commandCenter, /window\.metis\?\.diagnostics/);
  assert.match(commandCenter, /saveDiagnosticsBundle/);
  assert.match(css, /\.tool-activity-group/);
  assert.match(css, /\.tool-activity-row/);
  assert.match(css, /\.command-center-tabs/);
  assert.match(css, /\.command-status-grid/);
  assert.match(css, /\.command-run-row/);
  assert.match(smoke, /new94-tool-activity-group-visible/);
  assert.match(smoke, /new94-command-center-system-tab/);
  assert.match(smoke, /new94-command-center-provider-tab/);
  assert.match(doc, /Tool Activity Tree/);
  assert.match(doc, /Stop Condition/);
});

test('NEW-95 command center and tool activity polish stays wired', () => {
  const thread = [
    read('src/components/chat/MetisThread.tsx'),
    read('src/components/chat/ToolCallBlock.tsx'),
  ].join('\n');
  const commandCenter = read('src/components/command/CommandPalette.tsx');
  const css = read('src/index.css');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-95-Command-Center-And-Tool-Activity-Polish.md'),
    'utf8',
  );

  assert.doesNotMatch(thread, /ChatStatusStrip/);
  assert.match(thread, /ToolInlineDiffPreview/);
  assert.match(thread, /summarizeToolActivity/);
  assert.match(thread, /useState\(false\)/);
  assert.match(commandCenter, /command-center-pins/);
  assert.match(commandCenter, /CommandCenterOverview/);
  assert.match(commandCenter, /ToolStatsGrid/);
  assert.match(commandCenter, /setInterval\(\(\) => void refreshCenter\(true\),\s*4500\)/);
  assert.doesNotMatch(css, /\.chat-status-strip/);
  assert.match(css, /\.tool-inline-diff-line\[data-kind='add'\]/);
  assert.match(css, /\.tool-inline-diff-line\[data-kind='remove'\]/);
  assert.match(css, /\.command-center-overview/);
  assert.match(css, /\.command-tool-category-grid/);
  assert.match(css, /\.provider-model-list button/);
  assert.match(css, /@keyframes live-count-pop/);
  assert.match(css, /\.right-rail-inner header button:hover[\s\S]*transform:\s*none/);
  assert.match(doc, /Acceptance/);
});

test('NEW-96 independent side chat route and store stay isolated', () => {
  const uiStore = read('src/store/uiStore.ts');
  const sideStore = read('src/store/sideChatStore.ts');
  const sidePanel = read('src/components/rightrail/SideChatPanel.tsx');
  const api = read('src/lib/api.ts');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const fakeBackend = read('electron/backend.cjs');
  const realBackend = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'app.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-96-Independent-Right-Rail-Chat.md'),
    'utf8',
  );
  const sideRouteStart = realBackend.indexOf('@app.route("/side-chat"');
  const sideRouteEnd = realBackend.indexOf('@app.route("/chat"', sideRouteStart);
  const sideRoute = realBackend.slice(sideRouteStart, sideRouteEnd);

  assert.match(uiStore, /sideChatOpen:\s*boolean/);
  assert.match(uiStore, /setSideChatOpen/);
  assert.match(sidePanel, /独立上下文，不读取智能体任务、工具或工作区历史/);
  assert.match(sideStore, /STORAGE_KEY = 'metis\.sideChat\.sessions\.v2'/);
  assert.match(sideStore, /LEGACY_MESSAGES_KEY = 'metis\.sideChat\.messages\.v1'/);
  assert.match(sideStore, /sideChatStream/);
  assert.doesNotMatch(sideStore, /useChatStore|useSessionStore|useUiStore|chatStore|sessionStore|uiStore/);
  assert.doesNotMatch(sideStore, /startChatRun|runEventStream|chatStream\(/);
  assert.match(api, /function sideChatStream/);
  assert.match(api, /\/side-chat/);
  assert.match(fakeBackend, /handleFakeSideChat/);
  assert.match(fakeBackend, /pathname === '\/side-chat'/);
  assert.ok(sideRouteStart >= 0 && sideRouteEnd > sideRouteStart);
  assert.match(sideRoute, /build_agent_config\(system_prompt="", execution_mode="auto"\)/);
  assert.match(sideRoute, /backend\.chat_stream/);
  assert.doesNotMatch(sideRoute, /_prepare_chat_session|_save_session_history|_commit_request_history_to_active|_stream_agent_response|get_registry/);
  assert.match(css, /\.side-chat-pane/);
  assert.match(css, /\.side-chat-rail/);
  assert.match(css, /\.side-chat-message\[data-role='user'\]/);
  assert.match(smoke, /new96-side-chat-isolated/);
  assert.match(doc, /独立右栏 Chat/);
  assert.match(doc, /停止条件/);
});

test('NEW-97 and NEW-98 side chat rail, history, model override, and capsule toggles stay wired', () => {
  const uiStore = read('src/store/uiStore.ts');
  const sideStore = read('src/store/sideChatStore.ts');
  const sidePanel = read('src/components/rightrail/SideChatPanel.tsx');
  const appShell = read('src/components/shell/AppShell.tsx');
  const titlebar = read('src/components/shell/Titlebar.tsx');
  const statusbar = read('src/components/shell/Statusbar.tsx');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const app = read('src/App.tsx');
  const settings = readSettingsSources();
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const realBackend = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'app.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-97-Side-Chat-Dock-History-Model-And-Capsule-Toggles.md'),
    'utf8',
  );
  const doc98 = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-98-Side-Chat-Rail-Claude-Code-Layout.md'),
    'utf8',
  );
  const sideRouteStart = realBackend.indexOf('@app.route("/side-chat"');
  const sideRouteEnd = realBackend.indexOf('@app.route("/chat"', sideRouteStart);
  const sideRoute = realBackend.slice(sideRouteStart, sideRouteEnd);

  assert.match(uiStore, /sideChatOpen:\s*boolean/);
  assert.match(uiStore, /sideChatWidth:\s*number/);
  assert.match(uiStore, /setSideChatOpen/);
  assert.match(uiStore, /setSideChatWidth/);
  assert.match(uiStore, /metis\.sideChatWidth/);
  assert.match(app, /SideChatPanel/);
  assert.match(app, /defaultModel=\{settings\?\.model/);
  assert.doesNotMatch(app, /SideChatDock/);
  assert.match(appShell, /sideChat:\s*ReactNode/);
  assert.match(appShell, /data-side-chat=\{sideChatOpen\}/);
  assert.match(appShell, /data-side-chat-layout=\{sideChatLayoutOpen\}/);
  assert.match(appShell, /--side-chat-width/);
  assert.match(appShell, /side-chat-width-resizer/);
  assert.match(appShell, /className="side-chat-rail"/);
  assert.match(titlebar, /titlebar-chat-toggle/);
  assert.match(titlebar, /MessageCircle/);
  assert.doesNotMatch(titlebar, /title="设置"/);
  assert.doesNotMatch(titlebar, /setSettingsOpen/);
  assert.doesNotMatch(statusbar, /status-chat-launcher|ChevronUp|MessageCircle/);
  assert.doesNotMatch(rightRail, /mode === 'sidechat'|setMode\('sidechat'\)|SideChatPanel/);
  assert.match(sideStore, /sessions:\s*SideChatSession\[\]/);
  assert.match(sideStore, /createSession/);
  assert.match(sideStore, /renameSession/);
  assert.match(sideStore, /deleteSession/);
  assert.match(sideStore, /setSessionModel/);
  assert.match(sideStore, /model:\s*session\.model/);
  assert.doesNotMatch(sideStore, /useChatStore|useSessionStore|useUiStore|chatStore|sessionStore|uiStore/);
  assert.doesNotMatch(sideStore, /startChatRun|runEventStream|chatStream\(/);
  assert.match(sidePanel, /side-chat-history/);
  assert.match(sidePanel, /renameSession/);
  assert.match(sidePanel, /deleteSession/);
  assert.doesNotMatch(sidePanel, /side-chat-model-popover|side-chat-model-button|只影响独立 Chat|setSessionModel/);
  assert.ok(sideRouteStart >= 0 && sideRouteEnd > sideRouteStart);
  assert.match(sideRoute, /_side_chat_model_from_request/);
  assert.match(sideRoute, /replace\(config,\s*llm_model=model_override\)/);
  assert.doesNotMatch(sideRoute, /_prepare_chat_session|_save_session_history|_commit_request_history_to_active|_stream_agent_response|get_registry/);
  assert.match(settings, /capsule-toggle-row/);
  assert.match(css, /--side-chat-column-width:\s*min\(var\(--side-chat-width/);
  assert.match(css, /\.app-shell\[data-side-chat-layout='false'\] \.shell-body/);
  assert.match(css, /\.side-chat-rail/);
  assert.match(css, /\.side-chat-width-resizer/);
  assert.match(css, /\.side-chat-history/);
  assert.match(css, /\.side-chat-rail \.side-chat-history-list[\s\S]*display:\s*grid/);
  assert.match(css, /\.titlebar-chat-toggle/);
  assert.doesNotMatch(css, /\.status-chat-launcher/);
  assert.match(css, /\.capsule-toggle-row input:checked \+ i/);
  assert.match(smoke, /new98-titlebar-chat-toggle-visible/);
  assert.match(smoke, /new98-titlebar-settings-button-removed/);
  assert.match(smoke, /new98-statusbar-chat-launcher-removed/);
  assert.match(smoke, /new98-side-chat-rail-opens-from-titlebar/);
  assert.match(smoke, /new98-side-chat-rail-coexists-with-right-rail/);
  assert.match(smoke, /new98-side-chat-docks-right-without-right-rail/);
  assert.match(smoke, /new97-side-chat-history-create/);
  assert.match(smoke, /new97-side-chat-model-does-not-change-main-model/);
  assert.match(doc, /NEW-97/);
  assert.match(doc98, /NEW-98/);
  assert.match(doc98, /Side Chat must coexist with the right rail/);
  assert.match(doc, /停止条件/);
});

test('NEW-99 Claude Code workspace card deck stays wired', () => {
  const uiStore = read('src/store/uiStore.ts');
  const appShell = read('src/components/shell/AppShell.tsx');
  const titlebar = read('src/components/shell/Titlebar.tsx');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const terminal = read('src/components/terminal/TerminalPanel.tsx');
  const navRail = read('src/components/shell/NavRail.tsx');
  const statusbar = read('src/components/shell/Statusbar.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-99-Claude-Code-Workspace-Card-Deck.md'),
    'utf8',
  );

  assert.match(uiStore, /WorkspaceCardId = 'web' \| 'terminal' \| 'files' \| 'diff' \| 'activity' \| 'plan' \| 'tool'/);
  assert.match(uiStore, /workspaceCardVisibility:\s*WorkspaceCardVisibility/);
  assert.match(uiStore, /workspaceCardColumnWidths:\s*WorkspaceCardColumnWidths/);
  assert.match(uiStore, /workspaceCardRowSplits:\s*WorkspaceCardRowSplits/);
  assert.match(uiStore, /sidebarWidth:\s*number/);
  assert.match(uiStore, /setSidebarWidth/);
  assert.match(uiStore, /metis\.sidebarWidth/);
  assert.match(uiStore, /setWorkspaceCardVisible/);
  assert.match(uiStore, /toggleWorkspaceCard/);
  assert.match(uiStore, /setWorkspaceCardColumnWidths/);
  assert.match(uiStore, /setWorkspaceCardRowSplit/);
  assert.match(appShell, /<main className="main-panel">\{backendReady \? main : <ChatSkeleton \/>\}<\/main>/);
  assert.match(appShell, /sidebar-resizer/);
  assert.match(appShell, /startSidebarResize/);
  assert.doesNotMatch(appShell, /<TerminalPanel \/>/);
  assert.match(titlebar, /titlebar-cards-menu-button/);
  assert.match(titlebar, /titlebarWorkspaceCardOptions/);
  assert.match(titlebar, /toggleCardFromMenu/);
  assert.match(rightRail, /workspaceCardOptions/);
  assert.match(rightRail, /visibleWorkspaceColumns/);
  assert.match(rightRail, /label: 'Preview'/);
  assert.match(rightRail, /label: 'Terminal'/);
  assert.match(rightRail, /label: 'Files'/);
  assert.match(rightRail, /label: 'Diff'/);
  assert.match(rightRail, /label: 'Background tasks'/);
  assert.match(rightRail, /label: 'Plan'/);
  assert.match(rightRail, /workspaceCardColumns/);
  assert.doesNotMatch(rightRail, /workspace-card-empty-column/);
  assert.doesNotMatch(rightRail, /workspace-card-toolbar/);
  assert.doesNotMatch(rightRail, /workspace-card-menu-button/);
  assert.match(rightRail, /startColumnResize/);
  assert.match(rightRail, /startRowResize/);
  assert.match(rightRail, /workspace-column-resizer/);
  assert.match(rightRail, /workspace-row-resizer/);
  assert.match(rightRail, /TerminalPanel embedded/);
  assert.match(terminal, /embedded\?:\s*boolean/);
  assert.match(terminal, /data-embedded=\{embedded\}/);
  assert.match(navRail, /setTerminalOpen\(!terminalOpen\)/);
  assert.match(uiStore, /rightRailOpen: terminalOpen \? true : hasVisibleWorkspaceCard\(workspaceCardVisibility\)/);
  assert.doesNotMatch(statusbar, /status-chat-launcher|setTerminalOpen|setRightRailOpen/);
  assert.match(css, /\.workspace-card-deck/);
  assert.match(css, /\.workspace-card-column/);
  assert.match(css, /\.workspace-card-menu/);
  assert.match(css, /\.titlebar-cards-menu-button/);
  assert.match(css, /\.sidebar-resizer/);
  assert.match(css, /body\.resizing-sidebar/);
  assert.match(css, /\.workspace-column-resizer/);
  assert.match(css, /\.workspace-row-resizer/);
  assert.match(css, /\.terminal-panel\[data-embedded='true'\]/);
  assert.match(smoke, /new99-workspace-card-menu-visible/);
  assert.match(smoke, /new99-sidebar-resize-updates-width/);
  assert.match(smoke, /new99-terminal-card-opens-from-nav/);
  assert.match(smoke, /new99-card-close-expands-column/);
  assert.match(smoke, /new99-empty-column-unmounted/);
  assert.match(smoke, /new99-column-and-row-resize-updates-store/);
  assert.match(doc, /NEW-99 Claude Code Workspace Card Deck/);
  assert.match(doc, /停止条件/);
});

test('NEW-100 terminal, session, and card polish stays wired', () => {
  const api = read('src/lib/api.ts');
  const sessionStore = read('src/store/sessionStore.ts');
  const chatStore = read('src/store/chatStore.ts');
  const messageOps = read('src/store/messageOps.ts');
  const sidebar = read('src/components/sidebar/Sidebar.tsx');
  const terminal = read('src/components/terminal/TerminalPanel.tsx');
  const statusbar = read('src/components/shell/Statusbar.tsx');
  const settings = readSettingsSources();
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const sessionRoutes = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'session_routes.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-100-Terminal-Session-And-Card-Polish.md'),
    'utf8',
  );

  assert.match(api, /function renameSessionTitle/);
  assert.match(api, /\/sessions\/\$\{encodeURIComponent\(sessionId\)\}\/title/);
  assert.match(sessionStore, /renameSessionById/);
  assert.match(sessionRoutes, /@session_bp\.route\("\/sessions\/<session_id>\/title"/);
  assert.match(sidebar, /rename-session/);
  assert.match(sidebar, /session-rename-input/);
  assert.doesNotMatch(sidebar, /session-meta/);
  assert.match(terminal, /terminal-menu-check/);
  assert.match(terminal, /terminal-menu-rename/);
  assert.doesNotMatch(terminal, /Default shell|New terminal|data-shell/);
  assert.doesNotMatch(statusbar, /status-chat-launcher|setTerminalOpen|setRightRailOpen/);
  assert.match(settings, /默认终端/);
  assert.match(css, /\.workspace-card-header button/);
  assert.match(css, /\.right-rail-inner \.workspace-card-header\s*\{[\s\S]*grid-template-areas:\s*none/);
  assert.match(css, /\.right-rail-inner \.workspace-card-header button\s*\{[\s\S]*min-width:\s*0/);
  assert.match(css, /\.right-rail-inner \.workspace-card-header button\s*\{[\s\S]*border:\s*0/);
  assert.match(css, /\.context-compact-button/);
  assert.doesNotMatch(css, /\.session-meta/);
  assert.match(chatStore, /singleUserHistoryNotice/);
  assert.match(messageOps, /这个会话目前只保存了你的消息/);
  assert.match(smoke, /new100-session-rename-button-visible/);
  assert.match(smoke, /new100-session-naked-count-hidden/);
  assert.match(smoke, /new100-statusbar-terminal-button-removed/);
  assert.match(smoke, /new100-terminal-menu-no-default-shell/);
  assert.match(doc, /NEW-100 Terminal, Session, And Card Polish/);
  assert.match(doc, /停止条件/);
});

test('NEW-53 permission center productization stays wired', () => {
  const composer = read('src/components/chat/Composer.tsx');
  const settings = readSettingsSources();
  const api = read('src/lib/api.ts');
  const types = read('src/lib/types.ts');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const fakeBackend = read('electron/backend.cjs');
  const realBackend = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'app.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-53-Permission-Center-Productization.md'),
    'utf8',
  );

  assert.match(api, /createPermissionRule/);
  assert.match(api, /deletePermissionRule/);
  assert.match(api, /method:\s*'DELETE'/);
  assert.match(api, /getComposerPermissionMode/);
  assert.match(api, /setComposerPermissionMode/);
  assert.match(api, /composer_access/);
  assert.match(types, /interface PermissionRule/);
  assert.match(types, /PermissionAccessMode = 'ask' \| 'auto' \| 'full'/);
  assert.match(composer, /ComposerAccessMenu/);
  assert.match(composer, /composer-access-button/);
  assert.match(composer, /完全访问权限/);
  assert.match(settings, /权限中心/);
  assert.match(settings, /手动添加规则/);
  assert.match(settings, /permission-search-input/);
  assert.match(settings, /permission-new-tool-input/);
  assert.match(settings, /toolRisk/);
  assert.match(settings, /data-risk/);
  assert.match(css, /\.permission-summary-grid/);
  assert.match(css, /\.permission-create-rule/);
  assert.match(css, /\.permission-filter/);
  assert.match(css, /\.composer-access-menu/);
  assert.match(smoke, /new53-permission-center-visible/);
  assert.match(smoke, /composer-access-full-persists-rule/);
  assert.match(smoke, /composer-access-auto-removes-owned-rule/);
  assert.match(smoke, /new53-permission-manual-rule-created/);
  assert.match(smoke, /new53-permission-manual-rule-deleted/);
  assert.match(fakeBackend, /pathname === '\/permissions'/);
  assert.match(fakeBackend, /rule\.source === source/);
  assert.match(realBackend, /composer_access/);
  assert.match(doc, /Permission Center Productization/);
});

test('NEW-54 terminal workbench polish stays wired', () => {
  const navRail = read('src/components/shell/NavRail.tsx');
  const terminal = read('src/components/terminal/TerminalPanel.tsx');
  const uiStore = read('src/store/uiStore.ts');
  const settings = readSettingsSources();
  const smoke = read('src/runtime/rendererSmoke.ts');
  const css = read('src/index.css');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-54-Terminal-Workbench-Polish.md'),
    'utf8',
  );

  assert.match(navRail, /nav-terminal-button/);
  assert.match(navRail, /SquareTerminal/);
  assert.match(navRail, /setTerminalOpen\(!terminalOpen\)/);
  assert.match(terminal, /terminal-resizer/);
  assert.match(terminal, /onPointerDown=\{startResize\}/);
  assert.match(terminal, /terminal-tab-strip/);
  assert.match(terminal, /terminal-menu-trigger/);
  assert.match(terminal, /terminal-menu-delete/);
  assert.match(terminal, /terminal-menu-rename/);
  assert.match(terminal, /terminal-menu-check/);
  assert.doesNotMatch(terminal, /Rename terminal|New terminal|Default shell|data-shell/);
  assert.doesNotMatch(terminal, /PanelBottomClose|title="收起终端"|title="关闭终端"/);
  assert.match(terminal, /terminal-control-group/);
  assert.match(terminal, /setTerminalHeight/);
  assert.match(uiStore, /setTerminalHeight/);
  assert.match(settings, /terminal-settings-disclosure/);
  assert.match(settings, /terminal-shell-select/);
  assert.match(css, /\.terminal-resizer/);
  assert.match(css, /\.terminal-menu/);
  assert.match(css, /\.terminal-menu-rename/);
  assert.match(css, /\.terminal-menu-check/);
  assert.match(css, /\.terminal-control-group/);
  assert.match(css, /\.settings-disclosure/);
  assert.match(smoke, /new54-terminal-resize-updates-height/);
  assert.match(smoke, /new54-terminal-settings-disclosure/);
  assert.match(smoke, /terminal-nav-button-opens-terminal/);
  assert.match(smoke, /new100-terminal-menu-no-default-shell/);
  assert.match(doc, /Terminal Workbench Polish/);
});

test('NEW-58 interactive PTY terminal stays wired', () => {
  const main = read('electron/main.cjs');
  const preload = read('electron/preload.cjs');
  const terminal = read('src/components/terminal/TerminalPanel.tsx');
  const types = read('src/lib/types.ts');
  const globalTypes = read('src/global.d.ts');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const css = read('src/index.css');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-58-Interactive-PTY-Terminal.md'),
    'utf8',
  );

  assert.match(main, /terminalSessions = new Map/);
  assert.match(main, /require\('node-pty'\)/);
  assert.match(main, /createTerminalSession/);
  assert.match(main, /metis:terminal-create/);
  assert.match(main, /metis:terminal-input/);
  assert.match(main, /metis:terminal-resize/);
  assert.match(main, /metis:terminal-kill/);
  assert.match(preload, /terminalCreate/);
  assert.match(preload, /onTerminalEvent/);
  assert.match(globalTypes, /terminalCreate/);
  assert.match(types, /interface TerminalSessionPayload/);
  assert.match(types, /interface TerminalEventPayload/);
  assert.match(terminal, /terminal-live-output/);
  assert.match(terminal, /terminalInput/);
  assert.match(terminal, /terminalKill/);
  assert.match(terminal, /terminalResize/);
  assert.match(css, /\.terminal-live-output/);
  assert.match(css, /\.terminal-live-status/);
  assert.match(smoke, /new58-terminal-live-session-ready/);
  assert.match(smoke, /new58-terminal-live-output-streams/);
  assert.match(doc, /Interactive PTY Terminal/);
});

test('NEW-59 multi-file diff review workbench stays wired', () => {
  const uiStore = read('src/store/uiStore.ts');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const fileChangeReview = read('src/components/chat/FileChangeReviewCard.tsx');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const css = read('src/index.css');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-59-Multi-File-Diff-Review-Workbench.md'),
    'utf8',
  );

  assert.match(uiStore, /diffSummary:\s*FileChangeSummary\s*\|\s*null/);
  assert.match(uiStore, /activeDiffFileId:\s*string/);
  assert.match(uiStore, /diffRevertItems:\s*FileChangeRevertItem\[\]/);
  assert.match(uiStore, /setDiffReview/);
  assert.match(uiStore, /setActiveDiffFile/);
  assert.match(uiStore, /setDiffRevertItems/);
  assert.match(rightRail, /diff-file-navigator/);
  assert.match(rightRail, /diff-file-row/);
  assert.match(rightRail, /diff-revert-alert/);
  assert.match(rightRail, /diffRevertItemFor/);
  assert.match(fileChangeReview, /setDiffReview\(summary/);
  assert.match(fileChangeReview, /setDiffRevertItems\(summary\.id/);
  assert.match(fileChangeReview, /formatRevertFailure/);
  assert.match(fileChangeReview, /file-change-file-status/);
  assert.match(css, /\.diff-file-navigator/);
  assert.match(css, /\.diff-file-row/);
  assert.match(css, /\.diff-revert-alert/);
  assert.match(css, /\.file-change-file-status/);
  assert.match(smoke, /new59-diff-navigator-visible/);
  assert.match(smoke, /new59-diff-file-switches-active-preview/);
  assert.match(smoke, /new59-diff-navigator-revert-statuses/);
  assert.match(smoke, /new59-conflict-revert-card-detail/);
  assert.match(smoke, /new59-conflict-revert-rail-detail/);
  assert.match(doc, /Multi-File Diff Review Workbench/);
});

test('NEW-60 long run recovery diagnostics stays wired', () => {
  const types = read('src/lib/types.ts');
  const chatStore = read('src/store/chatStore.ts');
  const runRecovery = read('src/store/runRecovery.ts');
  const thread = read('src/components/chat/MetisThread.tsx');
  const noticeCards = read('src/components/chat/NoticeCards.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-60-Long-Run-Recovery-And-Session-Resume-Diagnostics.md'),
    'utf8',
  );

  assert.match(types, /interface ChatRunRecoverySnapshot/);
  assert.match(runRecovery, /RECOVERY_STORAGE_PREFIX/);
  assert.match(chatStore, /hydrateRecoverySnapshot/);
  assert.match(chatStore, /markRecoveryFailed/);
  assert.match(chatStore, /clearRecoverySnapshot/);
  assert.match(runRecovery, /recoveryPreview/);
  assert.match(thread, /RunRecoveryNotice/);
  assert.match(noticeCards, /run-recovery-notice/);
  assert.match(css, /\.run-recovery-notice/);
  assert.match(smoke, /new60-run-recovery-notice-visible/);
  assert.match(smoke, /new60-run-recovery-mark-failed/);
  assert.match(smoke, /new60-run-recovery-cleanup/);
  assert.match(doc, /Long Run Recovery/);
});

test('NEW-61 permission bulk management stays wired', () => {
  const settings = readSettingsSources();
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-61-Permission-Bulk-Management-And-Conflict-Cleanup.md'),
    'utf8',
  );

  assert.match(settings, /permission-bulk-toolbar/);
  assert.match(settings, /permission-import-export/);
  assert.match(settings, /permission-export-json/);
  assert.match(settings, /permission-import-json/);
  assert.match(settings, /conflictCleanupRuleIds/);
  assert.match(settings, /parsePermissionImport/);
  assert.match(settings, /onDeleteMany/);
  assert.match(css, /\.permission-bulk-toolbar/);
  assert.match(css, /\.permission-import-export/);
  assert.match(smoke, /new61-permission-bulk-toolbar-visible/);
  assert.match(smoke, /new61-permission-export-json/);
  assert.match(smoke, /new61-permission-import-persists-rule/);
  assert.match(smoke, /new61-permission-conflict-cleanup/);
  assert.match(smoke, /new61-permission-bulk-delete/);
  assert.match(doc, /Permission Bulk Management/);
});

test('NEW-62 real UI preview automation stays wired', () => {
  const webPreview = read('src/lib/webPreview.ts');
  const sseParser = read('src/store/sseParser.ts');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-62-Real-UI-Preview-Automation.md'),
    'utf8',
  );

  assert.match(webPreview, /LOCAL_PREVIEW_HOST_PORT_RE/);
  assert.match(webPreview, /ANSI_RE/);
  assert.match(webPreview, /normalizeLocalPreviewUrl/);
  assert.match(webPreview, /0\.0\.0\.0/);
  assert.match(webPreview, /127\.0\.0\.1/);
  assert.match(webPreview, /isPreviewableWebFilePath/);
  assert.match(webPreview, /localFilePreviewUrl/);
  assert.match(sseParser, /maybeOpenLocalPreview/);
  assert.match(smoke, /new62-vite-local-url-detected/);
  assert.match(smoke, /new62-host-port-url-normalized/);
  assert.match(smoke, /new62-external-url-rejected/);
  assert.match(smoke, /new62-right-rail-vite-preview-opens/);
  assert.match(smoke, /new74-local-html-preview-url-built/);
  assert.match(doc, /Real UI Preview Automation/);
});

test('NEW-63 release diagnostics bundle stays wired', () => {
  const types = read('src/lib/types.ts');
  const main = read('electron/main.cjs');
  const preload = read('electron/preload.cjs');
  const globalTypes = read('src/global.d.ts');
  const settings = readSettingsSources();
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-63-Release-Diagnostics-Bundle.md'),
    'utf8',
  );

  assert.match(types, /interface DiagnosticsPayload/);
  assert.match(types, /interface DiagnosticsBundleResult/);
  assert.match(main, /metis:diagnostics/);
  assert.match(main, /metis:save-diagnostics-bundle/);
  assert.match(main, /redactDiagnosticsText/);
  assert.match(main, /METIS_DESKTOP_SMOKE/);
  assert.match(preload, /diagnostics/);
  assert.match(preload, /saveDiagnosticsBundle/);
  assert.match(globalTypes, /DiagnosticsPayload/);
  assert.match(globalTypes, /saveDiagnosticsBundle/);
  assert.match(settings, /diagnostics-panel/);
  assert.match(settings, /生成诊断包/);
  assert.match(css, /\.diagnostics-panel/);
  assert.match(smoke, /new63-diagnostics-payload/);
  assert.match(smoke, /new63-diagnostics-bundle-generated/);
  assert.match(doc, /Release Diagnostics Bundle/);
});

test('NEW-64 dev server auto preview stays wired', () => {
  const types = read('src/lib/types.ts');
  const main = read('electron/main.cjs');
  const preload = read('electron/preload.cjs');
  const globalTypes = read('src/global.d.ts');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-64-Dev-Server-Auto-Preview.md'),
    'utf8',
  );

  assert.match(types, /interface DevServerStatus/);
  assert.match(types, /interface DevServerDetectResult/);
  assert.match(main, /detectFrontendProject/);
  assert.match(main, /findDevServerUrl/);
  assert.match(main, /findAvailableDevPort/);
  assert.match(main, /preferredDevServerPort/);
  assert.match(main, /METIS_PREVIEW_PORT/);
  assert.match(main, /--strictPort/);
  assert.match(main, /metis:dev-server-start/);
  assert.match(main, /METIS_DESKTOP_SMOKE/);
  assert.match(preload, /devServerStart/);
  assert.match(globalTypes, /devServerStatus/);
  assert.match(globalTypes, /onDevServerEvent/);
  assert.match(rightRail, /dev-server-panel/);
  assert.match(rightRail, /startDevPreview/);
  assert.match(rightRail, /setWebPreviewUrl\(payload\.status\.url\)/);
  assert.match(css, /\.dev-server-panel/);
  assert.match(smoke, /new64-dev-server-auto-opens-preview/);
  assert.match(doc, /Dev Server Auto Preview/);
});

test('NEW-65 right rail visual preview QA stays wired', () => {
  const types = read('src/lib/types.ts');
  const main = read('electron/main.cjs');
  const preload = read('electron/preload.cjs');
  const globalTypes = read('src/global.d.ts');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-65-Right-Rail-Visual-Preview-QA.md'),
    'utf8',
  );

  assert.match(types, /interface PreviewAuditResult/);
  assert.match(types, /interface PreviewAuditInput/);
  assert.match(main, /savePreviewEvidence/);
  assert.match(main, /metis:save-preview-evidence/);
  assert.match(preload, /savePreviewEvidence/);
  assert.match(globalTypes, /savePreviewEvidence/);
  assert.match(main, /capturePage/);
  assert.match(preload, /previewCapture/);
  assert.match(globalTypes, /previewCapture/);
  assert.match(rightRail, /previewCapture/);
  assert.match(rightRail, /auditActivePreview/);
  assert.match(rightRail, /preview-audit-panel/);
  assert.match(css, /\.preview-audit-panel/);
  assert.match(smoke, /new65-preview-audit-evidence-saved/);
  assert.match(doc, /Right Rail Visual Preview QA/);
});

test('NEW-66 true session resume stays wired', () => {
  const types = read('src/lib/types.ts');
  const chatStore = read('src/store/chatStore.ts');
  const messageOps = read('src/store/messageOps.ts');
  const noticeCards = read('src/components/chat/NoticeCards.tsx');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-66-True-Session-Resume.md'),
    'utf8',
  );

  assert.match(types, /canResume\?:\s*boolean/);
  assert.match(types, /checkpoint\?:\s*string/);
  assert.match(types, /lastUserPreview\?:\s*string/);
  assert.match(chatStore, /resumeInterruptedRun/);
  assert.match(chatStore, /buildResumePrompt/);
  assert.match(messageOps, /optionalRecoveryPreview/);
  assert.match(noticeCards, /继续执行/);
  assert.match(noticeCards, /中断点:/);
  assert.match(smoke, /new66-resume-sends-normal-chat/);
  assert.match(doc, /True Session Resume/);
});

test('NEW-67 context window quota bar stays wired', () => {
  const helper = read('src/lib/contextWindow.ts');
  const component = read('src/components/sidebar/ContextWindowBar.tsx');
  const sidebar = read('src/components/sidebar/Sidebar.tsx');
  const app = read('src/App.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-67-Context-Window-Quota-Bar.md'),
    'utf8',
  );

  assert.match(helper, /contextLimitForModel/);
  assert.match(helper, /deepseek-v4-flash/);
  assert.match(helper, /contextWindowLevel/);
  assert.match(component, /context-window-card/);
  assert.match(component, /上下文窗口|Context window/);
  assert.match(sidebar, /ContextWindowBar/);
  assert.match(app, /<Sidebar model=\{settings\?\.model/);
  assert.match(css, /\.context-window-card/);
  assert.match(css, /data-level='danger'/);
  assert.match(smoke, /new67-context-window-danger-threshold/);
  assert.match(doc, /Context Window Quota Bar/);
});

test('NEW-68 provider usage and model catalog stays wired', () => {
  const types = read('src/lib/types.ts');
  const api = read('src/lib/api.ts');
  const settings = readSettingsSources();
  const contextWindow = read('src/lib/contextWindow.ts');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const fakeBackend = read('electron/backend.cjs');
  const settingsRoutes = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'settings_routes.py'), 'utf8');
  const llmState = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'llm_state.py'), 'utf8');
  const registry = fs.readFileSync(path.resolve(root, '..', 'backend', 'bridges', 'provider_registry.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-68-Provider-Usage-And-Model-Catalog.md'),
    'utf8',
  );

  assert.match(types, /interface ProviderModelCatalog/);
  assert.match(types, /interface ProviderUsagePayload/);
  assert.match(types, /'usage'/);
  assert.match(api, /getProviderModels/);
  assert.match(api, /\/providers\/models/);
  assert.match(api, /getProviderUsage/);
  assert.match(api, /\/providers\/usage/);
  assert.match(settings, /刷新模型目录/);
  assert.match(settings, /额度 \/ Usage/);
  assert.match(settings, /provider-model-list/);
  assert.match(contextWindow, /gpt-5\.5/);
  assert.match(contextWindow, /codex-auto-review/);
  assert.match(css, /\.provider-usage-card/);
  assert.match(smoke, /new68-provider-usage-visible/);
  assert.match(fakeBackend, /fakeProviderModelCatalog/);
  assert.match(fakeBackend, /fakeProviderUsage/);
  assert.match(settingsRoutes, /@settings_bp\.route\("\/providers\/models"/);
  assert.match(settingsRoutes, /@settings_bp\.route\("\/providers\/usage"/);
  assert.match(llmState, /get_provider_models/);
  assert.match(llmState, /get_provider_usage/);
  assert.match(registry, /normalize_openai_api_base_url/);
  assert.match(doc, /Provider Usage And Model Catalog/);
});

test('NEW-69 context compaction and handoff control stays wired', () => {
  const types = read('src/lib/types.ts');
  const api = read('src/lib/api.ts');
  const chatStore = read('src/store/chatStore.ts');
  const runRecovery = read('src/store/runRecovery.ts');
  const component = read('src/components/sidebar/ContextWindowBar.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const fakeBackend = read('electron/backend.cjs');
  const realBackend = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'app.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-69-Context-Compaction-And-Handoff-Control.md'),
    'utf8',
  );

  assert.match(types, /interface CompactStatusPayload/);
  assert.match(types, /interface CompactHandoffSnapshot/);
  assert.match(api, /compactConversation/);
  assert.match(api, /\/compact/);
  assert.match(api, /getCompactStatus/);
  assert.match(chatStore, /compactContext/);
  assert.match(chatStore, /persistCompactHandoff/);
  assert.match(runRecovery, /metis\.chat\.compactHandoff\./);
  assert.match(component, /context-compact-button/);
  assert.match(component, /压缩上下文/);
  assert.match(css, /\.context-compact-status/);
  assert.match(smoke, /new69-context-compact-handoff-saved/);
  assert.match(fakeBackend, /fakeCompactSmokeSession/);
  assert.match(fakeBackend, /pathname === '\/compact'/);
  assert.match(fakeBackend, /pathname === '\/compact\/status'/);
  assert.match(realBackend, /@app\.route\("\/compact", methods=\["POST"\]\)/);
  assert.match(realBackend, /@app\.route\("\/compact\/status", methods=\["GET"\]\)/);
  assert.match(doc, /Context Compaction And Handoff Control/);
});

test('NEW-70 terminal scope and compaction visuals stay wired', () => {
  const appShell = read('src/components/shell/AppShell.tsx');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const thread = read('src/components/chat/MetisThread.tsx');
  const messageBubble = read('src/components/chat/MessageBubble.tsx');
  const noticeCards = read('src/components/chat/NoticeCards.tsx');
  const threadUtils = read('src/components/chat/threadUtils.ts');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-70-Terminal-Scope-And-Compaction-Visuals.md'),
    'utf8',
  );

  assert.match(appShell, /main-workspace-column/);
  assert.match(rightRail, /TerminalPanel embedded/);
  assert.match(css, /\.main-workspace-column/);
  assert.match(css, /\.terminal-panel\[data-embedded='true'\]/);
  assert.match(css, /\.thread-shell\[data-compacting='true'\] \.thread-window/);
  assert.match(css, /\.context-organizing-notice/);
  assert.match(css, /\.context-summary-card/);
  assert.match(thread, /data-compacting=\{compacting\}/);
  assert.match(noticeCards, /ContextOrganizingNotice/);
  assert.match(messageBubble, /ContextSummaryCard/);
  assert.match(threadUtils, /isContextSummary/);
  assert.match(messageBubble, /上下文已整理/);
  assert.match(smoke, /new99-terminal-card-scoped-to-workspace/);
  assert.match(smoke, /new70-context-compacting-visual-visible/);
  assert.match(smoke, /new70-context-summary-card-visible/);
  assert.match(doc, /Terminal Scope And Compaction Visuals/);
});

test('NEW-71 inline compaction animation stays wired', () => {
  const thread = read('src/components/chat/MetisThread.tsx');
  const noticeCards = read('src/components/chat/NoticeCards.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-71-Inline-Compaction-Animation.md'),
    'utf8',
  );

  assert.match(thread, /compacting=\{compacting\}/);
  assert.match(thread, /data-inline-compacting=\{compacting\}/);
  assert.match(noticeCards, /inline-compaction-row/);
  assert.match(noticeCards, /context-cube/);
  assert.match(noticeCards, /context-gold-rail/);
  assert.match(css, /\.inline-compaction-row/);
  assert.match(css, /\.context-box-stage/);
  assert.match(css, /\.context-cube-face\.front/);
  assert.match(css, /@keyframes context-box-hop/);
  assert.match(css, /@keyframes context-cube-spin/);
  assert.match(css, /@keyframes context-rail-sweep/);
  assert.match(smoke, /new71-inline-compaction-row-in-thread-window/);
  assert.match(smoke, /new71-inline-compaction-does-not-change-message-count/);
  assert.match(smoke, /new71-inline-compaction-row-clears-after-summary/);
  assert.match(doc, /Inline Compaction Animation/);
});

test('NEW-72 settings model and web preview polish stays wired', () => {
  const settings = readSettingsSources();
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-72-Settings-Model-And-Web-Preview-Polish.md'),
    'utf8',
  );

  assert.match(settings, /settings-base-url-input/);
  assert.match(settings, /settings-model-input/);
  assert.match(settings, /settings-api-key-input/);
  assert.match(settings, /spellCheck=\{false\}/);
  assert.match(settings, /provider-model-disclosure/);
  assert.match(settings, /modelCatalogOpen/);
  assert.match(settings, /setModelCatalogOpen\(true\)/);
  assert.match(rightRail, /previewLoad/);
  assert.match(rightRail, /previewSetBounds/);
  assert.match(rightRail, /data-compact=\{!showDevServerDetails\}/);
  assert.match(rightRail, /web-preview-host/);
  assert.match(css, /overflow-x:\s*hidden/);
  assert.match(css, /\.provider-model-disclosure/);
  assert.match(css, /\.dev-server-panel\[data-compact='true'\]/);
  assert.match(css, /repeat\(auto-fit,\s*minmax/);
  assert.match(smoke, /new72-provider-text-inputs-spellcheck-disabled/);
  assert.match(smoke, /new72-provider-model-picker-selects-model/);
  assert.match(smoke, /new72-settings-panel-no-horizontal-overflow/);
  assert.match(smoke, /new73-preview-view-ipc-enabled/);
  assert.match(doc, /Settings Model And Web Preview Polish/);
});

test('NEW-73 regression fixes stay wired', () => {
  const types = read('src/lib/types.ts');
  const api = read('src/lib/api.ts');
  const chatStore = read('src/store/chatStore.ts');
  const messageOps = read('src/store/messageOps.ts');
  const composer = read('src/components/chat/Composer.tsx');
  const messageBubble = read('src/components/chat/MessageBubble.tsx');
  const toolCallBlock = read('src/components/chat/ToolCallBlock.tsx');
  const threadUtils = read('src/components/chat/threadUtils.ts');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const terminal = read('src/components/terminal/TerminalPanel.tsx');
  const main = read('electron/main.cjs');
  const fakeBackend = read('electron/backend.cjs');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-73-Composer-Webview-Resize-Terminal-And-Settings-Fixes.md'),
    'utf8',
  );

  assert.match(types, /status\?:\s*'parsing' \| 'ready' \| 'error'/);
  assert.match(api, /readFileDataUrl/);
  assert.match(chatStore, /uploadDraft/);
  assert.match(chatStore, /replaceAttachmentDraft/);
  assert.match(chatStore, /attachmentReady/);
  assert.match(chatStore, /buildUserDisplayContent/);
  assert.match(messageOps, /parseUserAttachmentContent/);
  assert.match(composer, /composer-drop-zone/);
  assert.match(composer, /attachment-card/);
  assert.match(composer, /data-status=\{status\}/);
  assert.match(threadUtils, /messageAttachments/);
  assert.match(messageBubble, /message-attachment-card/);
  assert.match(messageBubble, /assistant-copy-button/);
  assert.match(threadUtils, /navigator\.clipboard/);
  assert.match(toolCallBlock, /localFilePreviewUrl/);
  assert.match(`${messageBubble}\n${toolCallBlock}`, /setWebPreviewUrl/);
  assert.match(rightRail, /setPointerCapture/);
  assert.match(rightRail, /resizing-rail/);
  assert.match(rightRail, /isPreviewableWebFilePath/);
  assert.match(rightRail, /previewSetBounds/);
  assert.match(terminal, /setPointerCapture/);
  assert.match(terminal, /resizing-terminal/);
  assert.match(terminal, /terminalCwd/);
  assert.match(main, /isPackagedBackendTerminalCwd/);
  assert.match(main, /decodeTerminalChunk/);
  assert.match(main, /gb18030/);
  assert.match(main, /emitTerminalExit/);
  assert.match(fakeBackend, /pathname === '\/upload\/parse'/);
  assert.match(css, /\.attachment-card/);
  assert.match(css, /\.composer-drop-zone/);
  assert.match(css, /\.message-attachment-card/);
  assert.match(css, /\.assistant-copy-button/);
  assert.match(css, /\.assistant-message-actions/);
  assert.match(css, /body\.resizing-rail/);
  assert.match(css, /body\.resizing-terminal/);
  assert.match(css, /\.permission-panel[\s\S]*overflow:\s*visible/);
  assert.match(smoke, /new73-composer-upload-ready-card/);
  assert.match(smoke, /new73-composer-upload-hidden-from-bubble/);
  assert.match(smoke, /new73-message-attachment-card-rendered/);
  assert.match(smoke, /assistant-copy-button-copies-text/);
  assert.match(smoke, /new74-local-html-write-auto-opens-web-preview/);
  assert.match(smoke, /new73-preview-view-ipc-enabled/);
  assert.match(smoke, /new73-rail-resize-disables-selection/);
  assert.match(smoke, /new73-terminal-cwd-not-backend-dist/);
  assert.match(smoke, /new73-permission-center-scrolls-vertically/);
  assert.match(doc, /NEW-73/);
});

test('NEW-75 assistant copy and provider preset stability stays wired', () => {
  const messageBubble = read('src/components/chat/MessageBubble.tsx');
  const threadUtils = read('src/components/chat/threadUtils.ts');
  const css = read('src/index.css');
  const types = read('src/lib/types.ts');
  const api = read('src/lib/api.ts');
  const commands = read('src/lib/commands.ts');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const providerContract = fs.readFileSync(path.resolve(root, '..', 'backend', 'bridges', 'provider_contract.py'), 'utf8');
  const providerProfiles = fs.readFileSync(path.resolve(root, '..', 'backend', 'bridges', 'provider_profiles.py'), 'utf8');
  const providerRegistry = fs.readFileSync(path.resolve(root, '..', 'backend', 'bridges', 'provider_registry.py'), 'utf8');
  const llmState = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'llm_state.py'), 'utf8');
  const providerTests = fs.readFileSync(path.resolve(root, '..', 'backend', 'tests', 'test_provider_model_catalog.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-75-Assistant-Copy-And-Provider-Preset-Stability.md'),
    'utf8',
  );

  assert.match(threadUtils, /copyTextToClipboard/);
  assert.match(messageBubble, /assistant-copy-button/);
  assert.match(css, /\.assistant-copy-button/);
  assert.match(smoke, /assistant-copy-button-copies-text/);
  assert.match(smoke, /new75-provider-preset-models-without-key/);
  assert.match(types, /modelContextWindows/);
  assert.match(api, /model_context_windows/);
  assert.match(commands, /backend:\s*'deepseek'/);
  assert.doesNotMatch(commands, /provider:\s*'DeepSeek'[\s\S]{0,120}backend:\s*'openai'/);
  assert.match(commands, /backend:\s*'kimi'/);
  assert.match(commands, /backend:\s*'zhipu-glm'/);
  assert.match(commands, /backend:\s*'bailian'/);
  assert.match(providerContract, /model_context_windows/);
  assert.match(providerProfiles, /provider_id=ProviderId\("kimi"\)/);
  assert.match(providerProfiles, /provider_id=ProviderId\("zhipu-glm"\)/);
  assert.match(providerProfiles, /provider_id=ProviderId\("bailian"\)/);
  assert.match(providerRegistry, /_ends_with_version_segment/);
  assert.match(llmState, /_models_url_candidates/);
  assert.match(llmState, /_provider_preset_model_result/);
  assert.match(llmState, /远程模型目录不可用，已使用本地预设模型/);
  assert.match(llmState, /尚未填写 API Key，已显示本地预设模型/);
  assert.match(providerTests, /test_provider_models_returns_deepseek_presets_without_api_key/);
  assert.match(providerTests, /test_models_url_candidates_preserve_version_segments/);
  assert.match(doc, /Assistant Copy And Provider Preset Stability/);
});

test('NEW-76 provider manager and model consistency stays wired', () => {
  const app = read('src/App.tsx');
  const settings = readSettingsSources();
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const llmState = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'llm_state.py'), 'utf8');
  const providerRegistry = fs.readFileSync(path.resolve(root, '..', 'backend', 'bridges', 'provider_registry.py'), 'utf8');
  const providerTests = fs.readFileSync(path.resolve(root, '..', 'backend', 'tests', 'test_provider_registry.py'), 'utf8');
  const catalogTests = fs.readFileSync(path.resolve(root, '..', 'backend', 'tests', 'test_provider_model_catalog.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-76-Provider-Manager-And-Model-Consistency.md'),
    'utf8',
  );

  assert.match(app, /<SettingsDialog onSaved=\{refresh\}/);
  assert.match(settings, /provider-profile-panel/);
  assert.match(settings, /repairProviderSettings/);
  assert.match(settings, /Provider ID/);
  assert.match(settings, /默认模型/);
  assert.match(settings, /本地预设/);
  assert.match(css, /\.provider-profile-panel/);
  assert.match(css, /\.provider-preset-strip/);
  assert.match(llmState, /_resolved_provider_runtime_values/);
  assert.match(providerRegistry, /normalize_provider_model/);
  assert.match(providerRegistry, /_looks_like_foreign_model/);
  assert.match(providerTests, /test_validate_provider_config_repairs_obvious_foreign_model/);
  assert.match(catalogTests, /test_deepseek_model_fallback_does_not_read_openai_model/);
  assert.match(smoke, /new76-deepseek-gpt-model-repaired/);
  assert.match(smoke, /new76-provider-profile-panel-visible/);
  assert.match(doc, /Provider Manager And Model Consistency/);
});

test('NEW-77 preview logs and compaction stability stays wired', () => {
  const main = read('electron/main.cjs');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const css = read('src/index.css');
  const fakeBackend = read('electron/backend.cjs');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-77-Preview-Logs-And-Compaction-Stability.md'),
    'utf8',
  );

  assert.match(rightRail, /web-preview-host/);
  assert.match(rightRail, /previewSetZoom/);
  assert.match(main, /setZoomFactor/);
  assert.match(css, /\.web-preview-host/);
  assert.match(css, /\.web-browser-toolbar\s*\{[\s\S]*grid-template-columns:\s*repeat\(3,\s*24px\)/);
  assert.match(css, /\.thread-shell\[data-compacting='true'\] \.thread-window[\s\S]*animation:\s*none/);
  assert.match(css, /\.context-summary-chip/);
  assert.match(fakeBackend, /classifyBackendLogSource/);
  assert.match(fakeBackend, /isWerkzeugInfo/);
  assert.match(smoke, /new77-web-zoom-layout-stable/);
  assert.match(smoke, /new77-context-compaction-window-does-not-animate/);
  assert.match(doc, /Preview Logs And Compaction Stability/);
});

test('NEW-78 OpenAI-compatible SSE UTF-8 decoding stays wired', () => {
  const common = fs.readFileSync(path.resolve(root, '..', 'backend', 'runtime', 'llm_backends', '_common.py'), 'utf8');
  const openaiCompat = fs.readFileSync(path.resolve(root, '..', 'backend', 'runtime', 'llm_backends', 'openai_compat.py'), 'utf8');
  const encodingTests = fs.readFileSync(path.resolve(root, '..', 'backend', 'tests', 'test_openai_compat_stream_encoding.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-78-OpenAI-Compatible-SSE-UTF8-Decoding.md'),
    'utf8',
  );

  assert.match(common, /def iter_utf8_lines/);
  assert.match(common, /decode\("utf-8-sig",\s*errors="replace"\)/);
  assert.match(openaiCompat, /iter_utf8_lines\(response(?:,\s*cancel_event=cancel_event)?\)/);
  assert.match(encodingTests, /ISO-8859-1/);
  assert.match(encodingTests, /你好，中文正常/);
  assert.match(doc, /OpenAI-Compatible SSE UTF-8 Decoding/);
});

test('NEW-79 cross-drive read and autosizing composer stays wired', () => {
  const agentLoop = fs.readFileSync(path.resolve(root, '..', 'backend', 'runtime', 'agent_loop.py'), 'utf8');
  const webApp = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'app.py'), 'utf8');
  const readFileTool = fs.readFileSync(
    path.resolve(root, '..', 'backend', 'tools', 'coding', 'read_search', 'read_single', 'read_file.py'),
    'utf8',
  );
  const pathTests = fs.readFileSync(path.resolve(root, '..', 'backend', 'tests', 'test_path_safety.py'), 'utf8');
  const permissionTests = fs.readFileSync(path.resolve(root, '..', 'backend', 'tests', 'test_permission_rules.py'), 'utf8');
  const composer = read('src/components/chat/Composer.tsx');
  const css = read('src/index.css');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-79-Cross-Drive-Read-And-Autosizing-Composer.md'),
    'utf8',
  );

  assert.match(agentLoop, /tool_boundary_overrides/);
  assert.match(webApp, /_tool_boundary_overrides/);
  assert.match(webApp, /allow_paths_outside_workspace/);
  assert.match(readFileTool, /def _read_docx_text/);
  assert.match(pathTests, /test_legacy_path_security_allows_outside_read_with_boundary_override/);
  assert.match(pathTests, /test_read_file_extracts_cross_workspace_docx_text_with_boundary_override/);
  assert.match(permissionTests, /test_composer_full_access_enables_cross_workspace_boundaries/);
  assert.match(composer, /textareaRef/);
  assert.match(composer, /scrollHeight/);
  assert.match(css, /max-height:\s*min\(38vh,\s*320px\)/);
  assert.match(doc, /Cross-Drive Read And Autosizing Composer/);
});

test('NEW-80 Codex-like composer layout stays wired', () => {
  const composer = read('src/components/chat/Composer.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-80-Codex-Like-Composer-Layout.md'),
    'utf8',
  );

  assert.match(composer, /composer-toolbar/);
  assert.match(composer, /composer-toolbar-left/);
  assert.match(composer, /composer-toolbar-right/);
  assert.match(composer, /<Plus size=\{22\}/);
  assert.match(composer, /<ArrowUp size=\{20\}/);
  assert.match(composer, /aria-label=\{streaming \? t\('停止生成'\) : t\('发送消息'\)\}/);
  assert.match(css, /\.composer\s*\{[\s\S]*flex-direction:\s*column/);
  assert.match(css, /--chat-column-width:\s*1104px/);
  assert.match(css, /\.thread-window\s*\{[\s\S]*width:\s*min\(var\(--chat-column-width\),\s*100%\)/);
  assert.match(css, /\.composer\s*\{[\s\S]*width:\s*min\(var\(--chat-column-width\),\s*100%\)/);
  assert.match(css, /\.send-button\s*\{[\s\S]*border-radius:\s*999px/);
  assert.match(smoke, /new80-composer-column-layout/);
  assert.match(smoke, /new80-composer-textarea-full-width/);
  assert.match(smoke, /new80-composer-circular-send-button/);
  assert.match(doc, /Codex-Like Composer Layout/);
});

test('NEW-81 composer sidebar and zone polish stays wired', () => {
  const sidebar = read('src/components/sidebar/Sidebar.tsx');
  const sections = read('src/components/sections/Sections.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-81-Composer-Sidebar-And-Zone-Polish.md'),
    'utf8',
  );

  assert.match(sidebar, /sidebar-search-row/);
  assert.match(sidebar, /sidebar-folder-button/);
  assert.doesNotMatch(sidebar, /MessageSquarePlus/);
  assert.doesNotMatch(sidebar, /<span>新会话<\/span>/);
  assert.match(sections, /meaningfulLog/);
  assert.match(sections, /meaningfulLog\.length > 0/);
  assert.match(sections, /zone-header-actions/);
  assert.match(css, /\.sidebar-search-row/);
  assert.match(css, /\.sidebar-folder-button/);
  assert.match(css, /min-height:\s*40px/);
  assert.match(css, /max-height:\s*min\(38vh,\s*320px\)/);
  assert.match(css, /\.zone-header-actions/);
  assert.match(css, /\.zone-pill\s*\{[\s\S]*border-radius:\s*10px/);
  assert.match(smoke, /new81-composer-default-height-compact/);
  assert.match(smoke, /new81-sidebar-folder-icon-in-search-row/);
  assert.match(smoke, /new81-sidebar-top-new-chat-removed/);
  assert.match(doc, /Composer Sidebar And Zone Polish/);
});

test('NEW-55 file change review card stays wired', () => {
  const diffPreview = read('src/lib/diffPreview.ts');
  const fileChangeReview = read('src/components/chat/FileChangeReviewCard.tsx');
  const messageBubble = read('src/components/chat/MessageBubble.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-55-File-Change-Review-And-Revert-UX.md'),
    'utf8',
  );

  assert.match(diffPreview, /interface FileChangeSummary/);
  assert.match(diffPreview, /summarizeFileChanges/);
  assert.match(diffPreview, /countDiffLines/);
  assert.match(messageBubble, /FileChangeReviewCard/);
  assert.match(fileChangeReview, /file-change-review-card/);
  assert.match(fileChangeReview, /file-change-undo-button/);
  assert.match(fileChangeReview, /file-change-review-button/);
  assert.match(fileChangeReview, /requestConfirm/);
  assert.match(fileChangeReview, /setDiffPreview/);
  assert.match(css, /\.file-change-review-card/);
  assert.match(css, /\.file-change-file-row/);
  assert.match(smoke, /new55-file-change-review-card-visible/);
  assert.match(smoke, /new55-file-change-review-button-opens-diff/);
  assert.match(smoke, /new55-file-change-undo-themed-dialog/);
  assert.match(doc, /File Change Review And Revert UX/);
});

test('NEW-56 transactional file revert stays wired', () => {
  const api = read('src/lib/api.ts');
  const types = read('src/lib/types.ts');
  const fileChangeReview = read('src/components/chat/FileChangeReviewCard.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const fakeBackend = read('electron/backend.cjs');
  const workspaceRoutes = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'workspace_routes.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-56-Transactional-File-Revert.md'),
    'utf8',
  );

  assert.match(types, /interface FileChangeRevertResult/);
  assert.match(api, /revertFileChanges/);
  assert.match(api, /\/workspace\/file-changes\/revert/);
  assert.match(fileChangeReview, /revertFileChanges\(summary\)/);
  assert.match(fileChangeReview, /撤销失败/);
  assert.match(css, /\.file-change-revert-message/);
  assert.match(smoke, /new56-file-change-revert-calls-backend/);
  assert.match(smoke, /new56-file-change-revert-success-count/);
  assert.match(fakeBackend, /pathname === '\/workspace\/file-changes\/revert'/);
  assert.match(fakeBackend, /fakePreflightFileChangeRevert/);
  assert.match(workspaceRoutes, /@workspace_bp\.route\("\/workspace\/file-changes\/revert"/);
  assert.match(workspaceRoutes, /_preflight_file_change_revert/);
  assert.match(workspaceRoutes, /validate_path_access\(abs_path, action="write"/);
  assert.match(workspaceRoutes, /file-change-transactions\.jsonl/);
  assert.match(doc, /Transactional File Revert/);
});

test('NEW-57 permission policy templates and path safety stay wired', () => {
  const settings = readSettingsSources();
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const api = read('src/lib/api.ts');
  const realBackend = fs.readFileSync(path.resolve(root, '..', 'backend', 'web', 'app.py'), 'utf8');
  const pathSafety = fs.readFileSync(path.resolve(root, '..', 'backend', 'runtime', 'path_safety.py'), 'utf8');
  const pathSafetyTest = fs.readFileSync(path.resolve(root, '..', 'backend', 'tests', 'test_path_safety.py'), 'utf8');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-57-Permission-Policy-Templates-And-Path-Safety.md'),
    'utf8',
  );

  assert.match(settings, /permissionPolicyTemplates/);
  assert.match(settings, /policy_template/);
  assert.match(settings, /permission-new-arg-key-input/);
  assert.match(settings, /permission-new-arg-pattern-input/);
  assert.match(settings, /pickFolder/);
  assert.match(settings, /选择文件夹/);
  assert.match(settings, /scopeLabel/);
  assert.match(css, /\.permission-policy-templates/);
  assert.match(css, /\.permission-template-button/);
  assert.match(api, /args_match:\s*payload\.argsMatch/);
  assert.match(api, /grant:\s*options\.grant/);
  assert.match(api, /root_path:\s*options\.rootPath/);
  assert.match(realBackend, /validate_tool_paths\(tool_name, arguments/);
  assert.match(realBackend, /temporary_root/);
  assert.match(realBackend, /suggested_writable_root/);
  assert.match(realBackend, /dict\(rule\.get\("args_match"\)/);
  assert.match(pathSafety, /suggested_root/);
  assert.match(pathSafety, /SECRET_EXTENSIONS/);
  assert.match(pathSafety, /SECRET_DIRS/);
  assert.match(pathSafetyTest, /test_path_safety_denies_ssh_directory_read/);
  assert.match(pathSafetyTest, /test_path_safety_denies_private_key_extension/);
  assert.match(smoke, /new57-permission-policy-templates-visible/);
  assert.match(smoke, /new57-permission-template-persists-scoped-rule/);
  assert.match(smoke, /new57-permission-manual-scoped-rule-created/);
  assert.match(doc, /Permission Policy Templates And Path Safety/);
});

test('transcript replay fixture and perf budgets stay no-secret and wired', () => {
  const fixture = read('src/runtime/fixtures/transcriptReplayFixture.ts');
  const rendererPerf = read('src/runtime/rendererPerf.ts');
  const perfBudgets = read('src/runtime/perfBudgets.ts');

  assert.match(fixture, /TRANSCRIPT_REPLAY_FIXTURE/);
  assert.match(fixture, /ordinaryQuestion/);
  assert.match(fixture, /markdownAnswer/);
  assert.match(fixture, /permissionTools/);
  assert.match(fixture, /waiting_approval/);
  assert.match(fixture, /subagents/);
  assert.match(fixture, /rightRailPreview/);
  assert.doesNotMatch(fixture, /sk-[A-Za-z0-9]{16,}/);
  assert.doesNotMatch(fixture, /api[_-]?key\s*[:=]/i);
  assert.match(rendererPerf, /measureFramesDuring/);
  assert.match(rendererPerf, /rightRailPreviewMs/);
  assert.match(rendererPerf, /droppedFramesOver32Ms/);
  assert.match(perfBudgets, /transcriptReplayDroppedFramesOver32Max/);
  assert.match(perfBudgets, /metrics\.transcriptReplay\.mountedRows/);
});

test('renderer source does not use system confirm dialogs', () => {
  const offenders = [];
  for (const file of listFiles('src')) {
    if (!/\.(ts|tsx|css)$/.test(file)) continue;
    const text = read(file);
    if (/window\.confirm|(?<!request)confirm\(/.test(text)) {
      offenders.push(file);
    }
  }
  assert.deepEqual(offenders, []);
});

test('chat stream keeps frame-coalesced text flushing', () => {
  const sseParser = read('src/store/sseParser.ts');
  assert.match(sseParser, /scheduleAssistantText/);
  assert.match(sseParser, /flushAssistantText/);
  assert.match(sseParser, /requestAnimationFrame/);
  assert.match(sseParser, /normalized\.kind === 'done'[\s\S]*flushAssistantText/);
});

test('chat thread keeps long-session window rendering and scroll anchoring', () => {
  const thread = read('src/components/chat/MetisThread.tsx');
  const css = read('src/index.css');
  assert.match(thread, /INITIAL_MESSAGE_WINDOW/);
  assert.match(thread, /ThreadPrimitive\.MessageByIndex/);
  assert.doesNotMatch(thread, /<ThreadPrimitive\.Messages/);
  assert.match(thread, /restoreScrollRef/);
  assert.match(thread, /BOTTOM_STICKY_THRESHOLD_PX/);
  assert.match(css, /\.thread-history-loader/);
  assert.match(css, /\.thread-window/);
});

test('project has durable compaction handoff summary', () => {
  const summaryPath = path.resolve(root, '..', 'docs', 'dev-log', 'PROJECT-HANDOFF-SUMMARY.md');
  const summary = fs.readFileSync(summaryPath, 'utf8');
  assert.match(summary, /durable resume point/i);
  assert.match(summary, /Metis Desktop/);
  assert.match(summary, /Verification Commands/);
});

test('agent event contract helper remains wired', () => {
  const api = read('src/lib/api.ts');
  const types = read('src/lib/types.ts');
  assert.match(api, /getAgentEventContract/);
  assert.match(types, /interface AgentEventContract/);
});

test('NEW-101 side chat seam and terminal delete fixes stay wired', () => {
  const sideChat = read('src/components/rightrail/SideChatPanel.tsx');
  const terminal = read('src/components/terminal/TerminalPanel.tsx');
  const css = read('src/index.css');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-101-Side-Chat-Seam-And-Terminal-Delete.md'),
    'utf8',
  );
  const deleteTerminalBlock = terminal.match(/const deleteTerminal = async[\s\S]*?\n  const renameTerminal =/)?.[0] || '';

  assert.match(sideChat, /SIDE_CHAT_HISTORY_COLLAPSED_HEIGHT\s*=\s*64/);
  assert.match(sideChat, /--side-chat-history-collapsed-height/);
  assert.match(css, /--side-chat-history-collapsed-height,\s*64px/);
  assert.match(css, /\.side-chat-rail \.side-chat-history\[data-open='false'\] header\s*\{[\s\S]*border-bottom:\s*0/);
  assert.match(terminal, /export function TerminalPanel\(\{ embedded = false, onRequestClose \}/);
  assert.match(deleteTerminalBlock, /setTerminals\(\[\]\)/);
  assert.match(deleteTerminalBlock, /onRequestClose\?\.\(\)/);
  assert.match(deleteTerminalBlock, /setTerminalOpen\(false\)/);
  assert.doesNotMatch(deleteTerminalBlock, /createLocalTerminal/);
  assert.doesNotMatch(terminal, /disabled=\{terminals\.length <= 1 && !terminal\.sessionId\}/);
  assert.match(doc, /Side Chat Seam And Terminal Delete/);
});

test('NEW-103 right rail density and preview simplification stays wired', () => {
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const css = read('src/index.css');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-103-Right-Rail-Density-And-Preview-Simplification.md'),
    'utf8',
  );

  assert.match(rightRail, /const \[devDetailsOpen, setDevDetailsOpen\]/);
  assert.match(rightRail, /const showDevServerDetails = devDetailsOpen && hasDevServerDetails/);
  assert.match(rightRail, /className="dev-server-details-button"/);
  assert.match(rightRail, /当前工作区未识别到可启动的前端项目/);
  assert.doesNotMatch(rightRail, /sessionCount:\s*number/);
  assert.doesNotMatch(rightRail, /workspaceCount:\s*number/);
  assert.doesNotMatch(rightRail, /<small>Sessions<\/small>/);
  assert.doesNotMatch(rightRail, /<small>Workspaces<\/small>/);
  // FABLEADV-28 后 .stability-grid 随稳定性总览一并移除（死类），删除其过期断言。
  assert.match(css, /\.dev-server-actions\s*\{[\s\S]*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(css, /\.preview-audit-panel\s*\{[\s\S]*grid-template-columns:\s*auto minmax\(0,\s*1fr\)/);
  assert.match(doc, /Right Rail Density And Preview Simplification/);
});

test('NEW-104 cardized side chat and preview toolbar stays wired', () => {
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-104-Cardized-Side-Chat-And-Preview-Toolbar.md'),
    'utf8',
  );

  assert.match(rightRail, /MoreVertical/);
  assert.match(rightRail, /className="web-more-button"/);
  assert.match(rightRail, /className="web-more-menu"/);
  assert.match(rightRail, /reloadActiveWeb/);
  assert.doesNotMatch(rightRail, /className="web-open-button"/);
  assert.doesNotMatch(rightRail, /className="web-zoom-in-button"/);
  assert.match(rightRail, /className="web-external-button"/);
  assert.match(css, /\.side-chat-rail\[data-open='true'\]\s*\{[\s\S]*padding:\s*6px/);
  assert.match(css, /\.side-chat-rail \.side-chat-pane\s*\{[\s\S]*border:\s*1px solid/);
  assert.match(css, /\.side-chat-rail \.side-chat-pane\s*\{[\s\S]*border-radius:\s*8px/);
  assert.match(css, /\.web-url-bar\s*\{[\s\S]*grid-template-columns:\s*auto auto minmax\(52px,\s*1fr\) auto auto auto/);
  assert.match(css, /\.web-more-menu/);
  assert.match(css, /\.web-browser-toolbar\s*\{[\s\S]*grid-template-columns:\s*repeat\(3,\s*24px\)/);
  assert.match(smoke, /web-more-menu/);
  assert.match(doc, /Cardized Side Chat And Preview Toolbar/);
});

test('NEW-105 preview omnibar and menu compaction stays wired', () => {
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const css = read('src/index.css');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-105-Preview-Omnibar-And-Menu-Compaction.md'),
    'utf8',
  );

  assert.match(rightRail, /className="web-url-nav-controls"/);
  assert.match(rightRail, /className="web-more-menu-label">\{t\('前端预览'\)/);
  assert.match(rightRail, /启动预览/);
  assert.match(rightRail, /视觉验收/);
  assert.doesNotMatch(rightRail, /className="web-more-menu-label">浏览器|网页加载中|网页状态/);
  assert.match(css, /\.web-preview-pane > \.dev-server-panel,\s*\n\.web-preview-pane > \.web-browser-toolbar,\s*\n\.web-preview-pane > \.rail-warning,\s*\n\.web-preview-pane > \.web-status-line\s*\{[\s\S]*display:\s*none/);
  assert.match(css, /\.web-url-bar\s*\{[\s\S]*grid-template-columns:\s*auto auto minmax\(52px,\s*1fr\) auto auto auto/);
  assert.match(css, /\.web-url-nav-controls\s*\{[\s\S]*grid-template-columns:\s*repeat\(3,\s*22px\)/);
  assert.match(css, /\.web-more-menu-wrap\s*\{[\s\S]*border-left:\s*1px solid/);
  assert.match(css, /\.web-more-status/);
  assert.match(doc, /Preview Omnibar And Menu Compaction/);
});

test('NEW-106 preview controls and context polish stays wired', () => {
  const sidePanel = read('src/components/rightrail/SideChatPanel.tsx');
  const contextWindow = read('src/components/sidebar/ContextWindowBar.tsx');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const css = read('src/index.css');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-106-Preview-Controls-And-Context-Polish.md'),
    'utf8',
  );

  assert.doesNotMatch(sidePanel, /side-chat-model-popover|side-chat-model-button|只影响独立 Chat|setSessionModel/);
  assert.match(rightRail, /className="web-zoom-controls"/);
  assert.match(rightRail, /className="web-external-wrap"/);
  assert.match(rightRail, /className="web-external-button"/);
  assert.doesNotMatch(rightRail, /网页加载中|网页状态|className="web-more-menu-label">浏览器/);
  assert.match(contextWindow, /<Gauge size=\{14\} strokeWidth=\{1\.9\}/);
  assert.match(css, /\.web-zoom-controls\s*\{[\s\S]*grid-template-columns:\s*22px 22px 32px/);
  assert.match(css, /\.web-external-wrap\s*\{[\s\S]*border-left:\s*1px solid/);
  assert.match(css, /\.web-more-menu button\s*\{[\s\S]*justify-content:\s*center/);
  assert.match(css, /\.context-window-head span svg\s*\{[\s\S]*overflow:\s*visible/);
  assert.match(smoke, /new98-side-chat-model-picker-removed/);
  assert.match(doc, /Preview Controls And Context Polish/);
});

test('NEW-107 webview popups route into preview card', () => {
  const main = read('electron/main.cjs');
  const preload = read('electron/preload.cjs');
  const globals = read('src/global.d.ts');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const smoke = read('src/runtime/rendererSmoke.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-107-Webview-Popup-In-Card-Playback.md'),
    'utf8',
  );

  assert.match(main, /WebContentsView/);
  assert.match(main, /routePreviewWindowOpen/);
  assert.match(main, /loadPreviewUrl\(value,\s*previewTabId\)/);
  assert.match(main, /webContents\.setWindowOpenHandler/);
  assert.match(main, /return \{ action: 'deny' \}/);
  assert.match(main, /metis:preview-state/);
  assert.match(main, /metis:preview-load/);
  assert.match(main, /isSafeExternalUrl\(value\)/);
  assert.match(preload, /previewLoad/);
  assert.match(preload, /onPreviewState/);
  assert.match(globals, /previewLoad/);
  assert.match(globals, /onPreviewState/);
  assert.match(rightRail, /onPreviewState/);
  assert.match(rightRail, /previewSetBounds/);
  assert.match(rightRail, /web-preview-host/);
  assert.doesNotMatch(main, /did-attach-webview|metis:webview-open-url|overrideBrowserWindowOptions/);
  assert.doesNotMatch(preload, /onWebviewOpenUrl|metis:webview-open-url/);
  assert.doesNotMatch(globals, /onWebviewOpenUrl/);
  assert.doesNotMatch(rightRail, /WEBVIEW_IN_PLACE_LINK_SCRIPT|executeJavaScript|createElement\(['"]webview['"]\)/);
  assert.match(smoke, /new107-preview-view-main-process-hosted/);
  assert.match(smoke, /new73-preview-view-ipc-enabled/);
  assert.match(doc, /Webview Popup In Card Playback/);
});

test('NEW-113 settings dialog performance refactor stays wired', () => {
  const settingsDialog = read('src/components/settings/SettingsDialog.tsx');
  const settingsSources = readSettingsSources();
  const permissionPanel = read('src/components/settings/PermissionPanel.tsx');
  const contracts = read('scripts/desktop-contract-tests.mjs');
  const css = read('src/index.css');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-113-Settings-Dialog-Performance-Fix.md'),
    'utf8',
  );
  const modalBlock = css.match(/\.modal-layer\s*\{[\s\S]*?\}/)?.[0] ?? '';

  assert.match(contracts, /function readSettingsSources/);
  assert.match(settingsSources, /memo\(function AppearanceTab/);
  assert.match(settingsSources, /memo\(function ModelTab/);
  assert.match(settingsSources, /memo\(function PermissionPanel/);
  assert.match(settingsDialog, /getProviderStatusCached/);
  assert.match(settingsDialog, /getPermissionsCached/);
  assert.match(settingsDialog, /active !== 'model'/);
  assert.match(settingsDialog, /active !== 'tools'/);
  assert.doesNotMatch(settingsDialog, /Promise\.all\(\[getSettings\(\),\s*getProviderStatus\(\)\]/);
  assert.match(permissionPanel, /useDeferredValue/);
  assert.match(permissionPanel, /filteredRules = useMemo/);
  assert.match(permissionPanel, /filteredAudit = useMemo/);
  assert.doesNotMatch(permissionPanel, /safeJson\(rule\.argsMatch\)/);
  assert.doesNotMatch(modalBlock, /backdrop-filter/);
  assert.match(css, /\.modal-layer\s*\{[\s\S]*background:\s*rgba\(0,\s*0,\s*0,\s*0\.45\)/);
  assert.match(css, /\.setup-layer\s*\{[\s\S]*backdrop-filter:\s*blur\(8px\)/);
  assert.match(css, /\.settings-dialog\s*\{[\s\S]*box-shadow:\s*0 16px 48px rgba\(0,\s*0,\s*0,\s*0\.22\)/);
  assert.match(doc, /NEW-113/);
});

test('NEW-112 project structure reorganization stays wired', () => {
  const repoRoot = path.resolve(root, '..');
  const launcher = read('electron/backend.cjs');
  const buildBackend = read('scripts/build-backend.ps1');
  const pyinstallerSpec = read('scripts/build-backend.spec');
  const ci = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'ci.yml'), 'utf8');
  const gitignore = fs.readFileSync(path.join(repoRoot, '.gitignore'), 'utf8');
  const backendPyproject = fs.readFileSync(path.join(repoRoot, 'backend', 'pyproject.toml'), 'utf8');

  assert.ok(fs.existsSync(path.join(repoRoot, 'desktop', 'package.json')));
  assert.ok(fs.existsSync(path.join(repoRoot, 'backend', '__main__.py')));
  assert.ok(fs.existsSync(path.join(repoRoot, 'backend', 'web', 'app.py')));
  assert.ok(fs.existsSync(path.join(repoRoot, 'backend', 'tools', 'registry.py')));
  assert.ok(fs.existsSync(path.join(repoRoot, 'backend', 'bridges', 'provider_registry.py')));
  assert.ok(fs.existsSync(path.join(repoRoot, 'docs', 'dev-log', 'NEW-112-Project-Structure-Reorganization.md')));
  assert.equal(fs.existsSync(path.join(repoRoot, 'miro')), false);
  assert.equal(fs.existsSync(path.join(repoRoot, 'metis-desktop')), false);

  assert.match(launcher, /path\.resolve\(__dirname, '\.\.', '\.\.', 'backend'\)/);
  assert.match(launcher, /'-m', 'backend', '--mode', 'web'/);
  assert.doesNotMatch(launcher, /python -m miro|'\.\.', '\.\.', 'miro'/);
  assert.match(buildBackend, /\$backendRoot = Join-Path \$repoRoot "backend"/);
  assert.match(pyinstallerSpec, /BACKEND_ROOT = REPO_ROOT \/ "backend"/);
  assert.match(pyinstallerSpec, /collect_submodules\("backend\.tools"\)/);
  assert.doesNotMatch(pyinstallerSpec, /MIRO_ROOT|hermes_bridge|\"Tools\"/);
  assert.match(backendPyproject, /name = "metis-backend"/);
  assert.match(backendPyproject, /metis-backend = "backend\.runtime\.cli:main"/);
  assert.match(ci, /cd desktop && npm ci/);
  assert.match(ci, /pip install -e backend\//);
  assert.match(ci, /python -m pytest backend\/tests\/ -q/);
  assert.match(gitignore, /desktop\/resources\/backend-dist\//);
  assert.match(gitignore, /backend\/var\//);
  assert.match(gitignore, /docs\/dev-log\//);
});

test('desktop launcher auto-heals a managed Python backend environment', () => {
  const launcher = read('electron/backend.cjs');
  const devLauncher = read('scripts/dev-launcher.mjs');
  const pkg = JSON.parse(read('package.json'));
  const readme = fs.readFileSync(path.resolve(root, '..', 'README.md'), 'utf8');
  const development = fs.readFileSync(path.resolve(root, '..', 'docs', 'DEVELOPMENT.md'), 'utf8');

  assert.match(launcher, /managedPythonRoot/);
  assert.match(launcher, /managedPythonExecutable/);
  assert.match(launcher, /bootstrapManagedPythonEnv/);
  assert.match(launcher, /Metis managed venv/);
  assert.match(launcher, /Windows py -3/);
  assert.match(launcher, /安装 Metis 后端依赖到托管环境/);
  assert.match(launcher, /托管环境未就绪，回退到现有 Python/);
  assert.match(launcher, /python -m pip install -e backend\//);
  assert.equal(pkg.scripts.dev, 'node scripts/dev-launcher.mjs');
  assert.match(devLauncher, /findAvailablePort/);
  assert.match(devLauncher, /Port .* is busy, using/);
  assert.match(devLauncher, /METIS_DESKTOP_DEV_SERVER/);
  assert.match(devLauncher, /waitForRenderer/);
  assert.match(readme, /npm run dev/);
  assert.match(development, /~\/\.metis\/python-backend\/venv|\.metis\\python-backend\\venv/);
});

test('NEW-116 and NEW-120 runtime and browser contracts stay wired', () => {
  const launcher = read('electron/backend.cjs');
  const api = read('src/lib/api.ts');
  const types = read('src/lib/types.ts');
  const browserAgent = fs.readFileSync(path.resolve(root, '..', 'backend', 'tools', 'browser_automation', 'browser_agent.py'), 'utf8');
  const browserTests = fs.readFileSync(
    path.resolve(root, '..', 'backend', 'tests', 'test_new_116_computer_browser_python_discovery.py'),
    'utf8',
  );
  const backendPyproject = fs.readFileSync(path.resolve(root, '..', 'backend', 'pyproject.toml'), 'utf8');
  const new116 = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-116-Computer-Use-Browser-Use-And-Python-Discovery.md'),
    'utf8',
  );
  const new120 = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-120-Runtime-Auto-Discovery-And-Install.md'),
    'utf8',
  );

  assert.match(launcher, /project venv/);
  assert.match(launcher, /CONDA_PREFIX/);
  assert.match(launcher, /CONDA_EXE/);
  assert.match(launcher, /LocalAppData Python/);
  assert.match(launcher, /Miniconda3/);
  assert.match(types, /visionProtocol:\s*string/);
  assert.match(api, /vision_protocol \?\? row\.visionProtocol/);
  assert.match(backendPyproject, /browser-use>=0\.13/);
  assert.match(browserAgent, /browser_use\.llm\.openai\.chat/);
  assert.match(browserAgent, /BrowserLLMConfig/);
  assert.match(browserAgent, /api_key_encrypted/);
  assert.match(browserAgent, /_format_browser_failure/);
  assert.match(browserAgent, /object\.__setattr__\(llm,\s*"provider"/);
  assert.match(browserTests, /test_fableadv_50_browser_use_native_openai_compatible_llm/);
  assert.match(browserTests, /test_fableadv_50_browser_use_errors_redact_secrets/);
  assert.match(new116, /Status:\s*Completed/);
  assert.match(new120, /Status:\s*Completed/);
});

test('composer auto-creates a session before first send', () => {
  const chatStore = read('src/store/chatStore.ts');

  assert.match(chatStore, /let sessionId = useSessionStore\.getState\(\)\.activeSessionId/);
  assert.match(chatStore, /if \(!sessionId\) \{[\s\S]{0,180}sessionId = await useSessionStore\.getState\(\)\.newSession\(\)/);
  assert.match(chatStore, /Metis 无法创建新会话，消息未发送/);
  assert.match(chatStore, /display: '无法创建会话'/);
});

test('returning to a live background run does not show recovery notice', () => {
  const chatStore = read('src/store/chatStore.ts');

  assert.match(chatStore, /const activeRunInfo = activeRun\.run && !TERMINAL_RUN_STATUSES\.has\(activeRun\.run\.status\) \? activeRun\.run : null/);
  assert.match(chatStore, /recoveryNotice: activeRunInfo \? null : recoverySnapshot/);
});

test('NEW-119 panel toggles close blank rails and avoid delayed layout rebound', () => {
  const appShell = read('src/components/shell/AppShell.tsx');
  const navRail = read('src/components/shell/NavRail.tsx');
  const uiStore = read('src/store/uiStore.ts');
  const doc = fs.readFileSync(
    path.resolve(root, '..', 'docs', 'dev-log', 'NEW-119-Panel-Toggle-Blank-Rail-And-Chat-Motion-Fix.md'),
    'utf8',
  );

  assert.match(uiStore, /setTerminalOpen: terminalOpen =>\s*set\(state => \{\s*const workspaceCardVisibility = persistWorkspaceCardVisibility/s);
  assert.match(uiStore, /rightRailOpen: terminalOpen \? true : hasVisibleWorkspaceCard\(workspaceCardVisibility\)/);
  assert.match(navRail, /onClick=\{\(\) => setTerminalOpen\(!terminalOpen\)\}/);
  assert.doesNotMatch(navRail, /setRightRailOpen\(true\)/);
  assert.match(appShell, /const sideChatLayoutOpen = sideChatOpen;/);
  assert.match(appShell, /const rightRailLayoutOpen = rightRailOpen;/);
  assert.doesNotMatch(appShell, /sideChatLayoutHold|rightRailLayoutHold/);
  assert.match(doc, /NEW-119/);
  assert.match(doc, /Completed/);
});

test('FABLEADV-12 skills system stays wired end-to-end', () => {
  const repoRoot = path.resolve(root, '..');
  const skillLoaderPath = path.join(repoRoot, 'backend', 'runtime', 'skill_loader.py');
  const skillLoader = fs.readFileSync(skillLoaderPath, 'utf8');
  const promptRuntime = fs.readFileSync(path.join(repoRoot, 'backend', 'core', 'engine', 'prompt_runtime.py'), 'utf8');
  const toolRegistry = fs.readFileSync(path.join(repoRoot, 'backend', 'runtime', 'tool_registry.py'), 'utf8');
  const runtimeToolProfiles = fs.readFileSync(path.join(repoRoot, 'backend', 'runtime', 'tool_profiles.py'), 'utf8');
  const featureRoutes = fs.readFileSync(path.join(repoRoot, 'backend', 'web', 'feature_routes.py'), 'utf8');
  const composer = read('src/components/chat/Composer.tsx');
  const sections = read('src/components/sections/Sections.tsx');
  const capabilitySuite = fs.readFileSync(path.join(repoRoot, 'backend', 'evals', 'suites', 'capability.json'), 'utf8');

  assert.ok(fs.existsSync(skillLoaderPath));
  assert.match(skillLoader, /def discover_skills/);
  assert.match(skillLoader, /def build_skills_index/);
  assert.match(skillLoader, /def load_skill_content/);
  assert.match(skillLoader, /def expand_user_skill_command/);
  assert.match(skillLoader, /\.metis/);
  assert.match(skillLoader, /disable-model-invocation/);
  assert.match(skillLoader, /user-invocable/);

  assert.match(promptRuntime, /skills_index/);
  assert.match(promptRuntime, /include_skills_index/);
  assert.match(promptRuntime, /build_skills_index/);
  assert.match(promptRuntime, /stability="session"/);
  assert.match(toolRegistry, /name="load_skill"/);
  assert.match(runtimeToolProfiles, /LEAN_PROFILE[\s\S]*"load_skill"/);

  for (const name of [
    'debug-workflow',
    'tdd-workflow',
    'code-review-checklist',
    'frontend-app',
    'git-discipline',
    'python-project',
  ]) {
    assert.ok(fs.existsSync(path.join(repoRoot, 'backend', 'resources', 'builtin_skills', name, 'SKILL.md')));
  }

  assert.match(featureRoutes, /discover_skills/);
  assert.match(featureRoutes, /"groups"/);
  assert.match(featureRoutes, /"builtin"/);
  assert.match(featureRoutes, /"project"/);
  assert.match(composer, /getSkills/);
  assert.match(composer, /slashSkillOptions/);
  assert.match(composer, /slash-skill-option/);
  assert.match(sections, /groupedSkills/);
  assert.match(sections, /source:\s*'builtin'/);
  assert.match(sections, /source:\s*'global'/);
  assert.match(sections, /source:\s*'project'/);
  assert.match(sections, /模型可触发|仅手动/);
  assert.match(capabilitySuite, /skills-debug-auto-load/);
  assert.match(capabilitySuite, /skills-frontend-self-verify/);
});

test('FABLEADV-13 preview refresh and chat linkify stay wired', () => {
  const repoRoot = path.resolve(root, '..');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const electronMain = read('electron/main.cjs');
  const threadUtils = read('src/components/chat/threadUtils.ts');
  const messageBubble = read('src/components/chat/MessageBubble.tsx');
  const webPreview = read('src/lib/webPreview.ts');
  const css = read('src/index.css');
  const remarkLinksPath = path.join(root, 'src', 'lib', 'remarkMetisLinks.ts');
  const metisLinksPath = path.join(root, 'src', 'lib', 'metisLinks.ts');
  const remarkLinksTestPath = path.join(root, 'src', 'lib', '__tests__', 'remarkMetisLinks.test.ts');
  const doc = fs.readFileSync(path.join(repoRoot, 'docs', 'dev-log', 'FABLEADV-13-Preview-Refresh-Linkify.md'), 'utf8');

  assert.match(rightRail, /void window\.metis\?\.previewLoad\?\.\(\{ tabId, url: webPreviewUrl \}\)/);
  assert.match(rightRail, /\}, \[activeWebPreviewId, webPreviewUrl\]\);/);
  assert.doesNotMatch(rightRail, /\}, \[activeWebPreviewId, syncPreviewBounds, updateWebPreviewTab, webPreviewUrl\]\);/);
  assert.match(electronMain, /previewLoadedUrls = new Map/);
  assert.match(electronMain, /previewLoadedUrls\.get\(nextTabId\) === value/);
  assert.match(electronMain, /skipped:\s*true/);

  assert.ok(fs.existsSync(remarkLinksPath));
  assert.ok(fs.existsSync(metisLinksPath));
  assert.ok(fs.existsSync(remarkLinksTestPath));
  assert.match(threadUtils, /remarkPlugins:\s*\[remarkGfm,\s*remarkMetisLinks\]/);
  assert.match(webPreview, /export function normalizeLocalPreviewUrl/);
  assert.match(messageBubble, /chatLinkActionFromHref/);
  assert.match(messageBubble, /getWorkspaceFile\(path\)/);
  assert.match(messageBubble, /setPreviewPath\(path\)/);
  assert.match(messageBubble, /localFilePreviewUrl\(await apiBase\(\), path\)/);
  assert.match(css, /\.markdown-body a\s*\{[\s\S]*border-bottom:\s*1px solid transparent/);
  assert.match(css, /\.markdown-body a\[data-link-kind='web'\]/);
  assert.match(doc, /Status\*\*:\s*Completed/);
});

test('FABLEADV-15 provider probe and FABLEADV-17 rewind stay wired', () => {
  const repoRoot = path.resolve(root, '..');
  const providerManager = read('src/components/settings/ProviderRegistryManager.tsx');
  const chatStore = read('src/store/chatStore.ts');
  const messageBubble = read('src/components/chat/MessageBubble.tsx');
  const composer = read('src/components/chat/Composer.tsx');
  const commands = read('src/lib/commands.ts');
  const commandPalette = read('src/components/command/CommandPalette.tsx');
  const app = read('src/App.tsx');
  const rightRail = read('src/components/rightrail/RightRail.tsx');
  const uiStore = read('src/store/uiStore.ts');
  const css = read('src/index.css');
  const api = read('src/lib/api.ts');
  const types = read('src/lib/types.ts');
  const settingsRoutes = fs.readFileSync(path.join(repoRoot, 'backend', 'web', 'settings_routes.py'), 'utf8');
  const webApp = fs.readFileSync(path.join(repoRoot, 'backend', 'web', 'app.py'), 'utf8');
  const checkpoints = fs.readFileSync(path.join(repoRoot, 'backend', 'runtime', 'checkpoints.py'), 'utf8');
  const providerRegistry = fs.readFileSync(path.join(repoRoot, 'backend', 'bridges', 'provider_registry.py'), 'utf8');

  assert.match(settingsRoutes, /\/providers\/registry\/<provider_id>\/probe/);
  assert.match(settingsRoutes, /run_provider_conformance_probe/);
  assert.match(settingsRoutes, /get_provider_models/);
  assert.match(providerRegistry, /def is_builtin_provider_id/);
  assert.match(providerManager, /probeProviderRegistry/);
  assert.match(providerManager, /className="provider-registry-probe"/);
  assert.match(providerManager, /临时 API Key（仅探测）/);
  assert.match(api, /export async function probeProviderRegistry/);
  assert.match(types, /interface ProviderRegistryProbeResult/);
  assert.match(css, /\.provider-registry-probe/);

  assert.ok(fs.existsSync(path.join(repoRoot, 'backend', 'runtime', 'checkpoints.py')));
  assert.match(checkpoints, /class CheckpointRecorder/);
  assert.match(checkpoints, /capture_tool_call/);
  assert.match(checkpoints, /restore_files_from_checkpoint/);
  assert.match(webApp, /\/sessions\/<session_id>\/checkpoints/);
  assert.match(webApp, /\/sessions\/<session_id>\/rewind/);
  assert.match(webApp, /create_checkpoint/);
  assert.match(webApp, /checkpoint\.capture_tool_call/);
  assert.match(api, /export async function getSessionCheckpoints/);
  assert.match(api, /export async function rewindSession/);
  assert.match(types, /interface SessionCheckpoint/);
  assert.match(types, /interface RewindResult/);
  assert.match(chatStore, /rewindLatest/);
  assert.match(chatStore, /rewindToMessage/);
  assert.match(chatStore, /text\.toLowerCase\(\) === '\/rewind'/);
  assert.match(messageBubble, /回到这里/);
  assert.match(messageBubble, /user-rewind-button/);
  assert.match(composer, /\/rewind/);
  assert.match(commands, /conversation\.rewind/);
  assert.match(commandPalette, /rewindConversation/);
  assert.match(app, /lastEscapeAt/);
  assert.match(rightRail, /workspaceRefreshNonce/);
  assert.match(uiStore, /refreshWorkspaceView/);
  assert.match(css, /\.user-rewind-button/);
});
