import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(__dirname, '..');
const repoRoot = path.resolve(desktopRoot, '..');

const suites = [
  {
    id: 'permissions',
    title: '权限矩阵 / permission matrix',
    command: 'python',
    args: [
      '-m',
      'pytest',
      'backend/tests/test_permission_rules.py',
      'backend/tests/test_permission_control_plane.py',
      'backend/tests/test_path_safety.py',
      '-q',
    ],
    cwd: repoRoot,
  },
  {
    id: 'compact',
    title: 'compact 回放 / compaction replay',
    command: 'python',
    args: [
      '-m',
      'pytest',
      'backend/tests/test_fableadv_10_compaction_transcript_separation.py',
      'backend/tests/test_context_control.py',
      'backend/tests/test_result_compactor.py',
      '-q',
    ],
    cwd: repoRoot,
  },
  {
    id: 'verifier',
    title: 'verifier 反例 / evidence counterexample',
    command: 'python',
    args: [
      '-m',
      'pytest',
      'backend/tests/test_verifier_evidence_chain.py',
      'backend/tests/test_preview_browser_bridge.py::test_preview_browser_verify_supports_browser_verifier',
      'backend/tests/test_preview_browser_bridge.py::test_preview_browser_verify_extracts_success_prompt_from_assertion',
      'backend/tests/test_win2_computer_use.py::test_win2_verify_returns_checks_and_evidence',
      '-q',
    ],
    cwd: repoRoot,
  },
  {
    id: 'browser-computer',
    title: 'browser/computer-use 端到端 / action loop',
    command: 'python',
    args: [
      '-m',
      'pytest',
      'backend/tests/test_preview_browser_bridge.py',
      'backend/tests/test_win2_computer_use.py',
      'backend/tests/test_fableadv_20_computer_use.py',
      '-q',
    ],
    cwd: repoRoot,
  },
  {
    id: 'artifacts',
    title: 'PDF/DOCX 渲染验收 / artifact rendering',
    command: 'python',
    args: ['-m', 'pytest', 'backend/tests/test_artifact_pdf_docx_tools.py', '-q'],
    cwd: repoRoot,
  },
  {
    id: 'model-tools',
    title: '模型工具调用兼容性 / model tool compatibility',
    command: 'python',
    args: [
      '-m',
      'pytest',
      'backend/tests/test_provider_registry.py',
      'backend/tests/test_provider_model_catalog.py',
      'backend/tests/test_runtime_tool_registry_metadata.py',
      'backend/tests/test_agent_runtime_reliability.py',
      'backend/tests/test_deepseek_strict_schema.py',
      'backend/tests/test_openai_compat_stream_encoding.py',
      'backend/tests/test_fableadv_40_model_tool_routing.py',
      'backend/tests/test_fableadv_42_deepseek_beta_endpoint.py',
      'backend/tests/test_fableadv_43_reasoning_optin.py',
      '-q',
    ],
    cwd: repoRoot,
  },
  {
    id: 'desktop-contracts',
    title: '桌面 wiring contract / UI integration',
    command: 'node',
    args: ['--test', 'scripts/desktop-contract-tests.mjs', 'electron/preview-state.test.cjs'],
    cwd: desktopRoot,
  },
];

const args = process.argv.slice(2);

if (args.includes('--list')) {
  for (const suite of suites) {
    process.stdout.write(`${suite.id}\t${suite.title}\n`);
  }
  process.exit(0);
}

const requested = selectedSuiteIds(args);
const selected = requested.length
  ? suites.filter(suite => requested.includes(suite.id))
  : suites;

const missing = requested.filter(id => !suites.some(suite => suite.id === id));
if (missing.length > 0) {
  process.stderr.write(`[fixed-regression] Unknown suite(s): ${missing.join(', ')}\n`);
  process.stderr.write('[fixed-regression] Run with --list to see available suites.\n');
  process.exit(1);
}

let failed = 0;
for (const suite of selected) {
  const code = await runSuite(suite);
  if (code !== 0) failed += 1;
}

if (failed > 0) {
  process.stderr.write(`[fixed-regression] ${failed}/${selected.length} suite(s) failed.\n`);
  process.exit(1);
}

process.stdout.write(`[fixed-regression] ${selected.length} suite(s) passed.\n`);

function selectedSuiteIds(cliArgs) {
  const fromEnv = (process.env.METIS_FIXED_REGRESSION_SUITE || '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean);
  const fromCli = [];
  for (let index = 0; index < cliArgs.length; index += 1) {
    const arg = cliArgs[index];
    if (arg === '--suite' && cliArgs[index + 1]) {
      fromCli.push(...cliArgs[index + 1].split(',').map(item => item.trim()).filter(Boolean));
      index += 1;
    } else if (arg.startsWith('--suite=')) {
      fromCli.push(...arg.slice('--suite='.length).split(',').map(item => item.trim()).filter(Boolean));
    } else if (!arg.startsWith('-')) {
      fromCli.push(...arg.split(',').map(item => item.trim()).filter(Boolean));
    }
  }
  return [...new Set([...fromEnv, ...fromCli])];
}

function runSuite(suite) {
  return new Promise(resolve => {
    process.stdout.write(`\n[fixed-regression] ${suite.title}\n`);
    process.stdout.write(`[fixed-regression] ${suite.command} ${suite.args.join(' ')}\n`);
    const child = spawn(suite.command, suite.args, {
      cwd: suite.cwd,
      env: { ...process.env, PYTHONUTF8: '1' },
      shell: process.platform === 'win32',
      stdio: 'inherit',
    });
    child.on('close', code => resolve(code ?? 1));
    child.on('error', error => {
      process.stderr.write(`[fixed-regression] Could not start ${suite.id}: ${error.message}\n`);
      resolve(1);
    });
  });
}
