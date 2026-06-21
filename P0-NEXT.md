# P0-NEXT

Date: 2026-06-21

This document records the next urgent fixes and the feature research notes for Metis. It is ordered by user-visible risk first, then by product upgrades.

## Tonight Completed - Claude Read This First

If Claude only has one minute, read this section first. Tonight's completed work:

- P0 model/runtime fixes:
  - Fixed explicit model selection visibility/debug path so selected model, routed model, requested model, served model, and fallback model can be traced.
  - Added runtime/VM self-test debug summaries so "ready but self-test failed" explains the likely cause.
  - Added fixed-regression coverage around DeepSeek strict schema, runtime manager, and permission center failures.

- Public/diagnostic split:
  - Added PostToolUse sanitizer.
  - Tool cards and model context now receive public-safe summaries.
  - Local audit keeps diagnostic raw detail with secrets redacted.
  - Goal: stop normal chat from leaking VM/runtime internals while preserving debugging evidence.

- Tool debug instrumentation:
  - Model actual-call debug.
  - Runtime/VM self-test debug.
  - Preview Browser debug.
  - Computer Use task-end debug.
  - File parsing debug for doc/docx/pdf/xlsx-style failures.

- UI polish:
  - User message hover actions: copy + rewind icon buttons.
  - Context window color hierarchy: gold-tiered token breakdown.
  - Sidebar workspace rows: folder icon added; workspace color strip no longer extends through sessions.

- Theme palette update:
  - Added `Moonlit Alabaster / 月白圣廊`: cold white theme, black accents.
  - Added `Nocturne Obsidian / 黑曜夜曲`: pure-black-led dark theme, pearl accents.
  - Added `Mistbound Jade / 雾隐青玉`: cool light jade theme.
  - Added `Crimson Reliquary / 绯红圣匣`: muted dark crimson theme, intentionally not bright red.

- Product direction note:
  - Added a direct benchmark note for Claude comparing Claude Code, Codex, and Hermes.
  - Main recommendation: stop piling on tools; first make existing tools trustworthy through permissions, routing, runtime boundaries, verifier/evidence, public summaries, diagnostic raw store, and clean terminal states.

## Research Sources

External sources checked:

- Claude Code official changelog: https://code.claude.com/docs/en/changelog
- Anthropic Claude Code GitHub releases: https://github.com/anthropics/claude-code/releases
- ClaudeCodeLog on X: https://x.com/ClaudeCodeLog
- OpenAI Codex changelog: https://developers.openai.com/codex/changelog
- OpenAI Codex GitHub releases: https://github.com/openai/codex/releases
- CodexReleases on X: https://x.com/CodexReleases
- Hermes Agent skills catalog: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/skills-catalog.md
- Hermes Agent GitHub: https://github.com/NousResearch/hermes-agent

Notes:

- X pages are not reliably readable without an authenticated browser session. I treated the X accounts as discovery feeds, then used official changelogs, GitHub releases, and indexed snippets for verifiable content.
- The most relevant Claude Code entries are 2.1.185 and 2.1.183.
- The most relevant Codex entries are 2026-06-18, 2026-06-16, 2026-06-11, 2026-04-16, 2026-03-18, and 2026-02-02.
- Hermes research focused on skills, skill lifecycle issues, skill bloat, protected skills, and self-evolving skill roles.

## P0 Order

### P0-1. Fix Explicit Model Selection Being Overridden

User-visible bug:

- UI shows `deepseek-v4-pro`.
- Composer model selector also shows `v4-pro`.
- Assistant replies that it is `DeepSeek V4 Flash`.

Likely root cause:

- `backend/runtime/model_router.py` maps normal chat to role `fast`.
- `_select_model_for_role(role="fast")` prefers model names containing `flash`, `mini`, `haiku`, `turbo`, or `lite`.
- So a normal "what model are you" chat can be routed from explicit Pro to Flash.

Required fix:

1. Add an explicit model lock.
   - If the user selects a concrete model in the composer, Metis must use that model.
   - Auto routing may only switch models when the selector is explicitly in Auto/Fast mode.
   - If a fallback happens because the selected model failed, emit a visible "fallback used" event.

2. Track actual runtime model.
   - Every assistant run should store:
     - requested model
     - routed model
     - actual provider response model if available
     - fallback reason
   - UI should display actual run model for that message, not just global selected model.

3. Harden model-identity answers.
   - If asked "你是什么模型", answer from runtime metadata.
   - Do not guess "Flash" or "Pro" from prompt text.
   - If actual provider model is unknown, say: "当前会话选择的是 X；服务端未返回更细的模型身份。"

Acceptance:

- With `deepseek-v4-pro` selected, asking "你是什么模型" must not answer Flash.
- If router changes the model, a runtime status must say exactly why.
- Add a fixed regression for explicit model lock.

### P0-2. Stop Leaking Internal Runtime / VM Details Into Normal Replies

User-visible bug:

- Asking "现在虚拟机有没有问题?" produced low-level details such as Docker daemon status, rootfs.vhdx, vmlinuz, initrd, metis-bin.vhdx, sessiondata.vhdx.
- This exposes implementation internals in a normal chat answer.

Root cause:

- Tool outputs and route/runtime hints contain internal implementation details.
- Current system prompt does not clearly separate public user answers from maintainer diagnostics.
- The model is allowed to summarize raw tool output directly.

Required fix:

1. Add public/private runtime wording.
   - Public answer: "沙箱可用 / 需要修复 / 可回退本地执行".
   - Diagnostic drawer/log: exact Docker, WSL, HCS, VHDX, kernel, initrd details.

2. Add system prompt rule:
   - Do not reveal internal implementation details, file names, asset names, service names, protocol names, private paths, or low-level infrastructure unless the user explicitly asks for diagnostics or development details.
   - For normal users, summarize capability and next action.

3. Add response redaction before assistant-visible summaries where possible.
   - Keep detailed tool results in logs.
   - Feed the model a compact public status for ordinary chat.

Acceptance:

- "虚拟机有没有问题?" returns a short health summary.
- "展开诊断细节" or "我是开发者，给我内部状态" may include low-level details.
- Add a regression prompt that rejects leaking `rootfs.vhdx`, `vmlinuz`, `initrd`, `metis-bin.vhdx`, `sessiondata.vhdx` in ordinary answers.

### P0-3. Fix Runtime Self-Test Contradiction

User-visible bug:

- Runtime page says "HCS 沙箱环境 / Sandbox ready".
- It also says "自检失败: 沙箱回退到本地执行(VM 未真正运行)".
- Runtime Pack says installed and SHA passed, but Guest is "未就绪".

Current local code signal:

- `backend/runtime/runtime_manager.py::runtime_manager_selftest()` forces `backend="hcs"`.
- It only passes when backend is exactly `hcs`, return code is 0, and stdout contains both `SELFTEST_BOOT_OK` and `SELFTEST_XLSX_OK`.
- That strict self-test is good, but the UI health language is too optimistic before guest/HCS is actually verified.

Required fix:

1. Split status labels:
   - "Host prerequisites ready"
   - "Runtime pack installed"
   - "Guest protocol ready"
   - "HCS VM verified"

2. Only show top-level "ready" when:
   - runtime pack assets are verified
   - guest protocol is ready
   - actual HCS self-test passes

3. If WSL/local fallback is available, label it as fallback, not VM-ready.

4. Self-test output should say:
   - what passed
   - what failed
   - one next action
   - no raw internal asset list in normal UI

Acceptance:

- Installed pack + failed guest handshake cannot show "Sandbox ready".
- Failed HCS self-test must not be visually green.
- Diagnostics export still contains full internal details.

### P0-4. Fix the Three Existing Fixed-Regression Failures

Current failures from `npm run -s test:contracts`:

1. DeepSeek strict schema sanitizer
   - Contract expects strict closed object schema: `additionalProperties = False`.
   - Current implementation allows free-form empty objects with `additionalProperties = True`.
   - Decide the correct behavior by real DeepSeek strict-mode compatibility, then update code or test.
   - P0 default: keep strict schema closed unless a tool explicitly needs free-form input.

2. Runtime Manager productization
   - Contract expects `resources/runtime-pack`.
   - Current packaging intentionally removed bundled runtime pack and uses first-run download.
   - Fix the test to accept first-run download mode, or restore optional bundled pack support without forcing installer size to 1GB+.

3. Permission Center productization
   - Contract expects old `PermissionAccessMode = 'ask' | 'auto' | 'full'`.
   - Current permission model appears to have evolved.
   - Either add a compatibility alias or update the contract to the new permission type.

Acceptance:

- `npm run -s test:contracts` has zero failures.
- The P0 fixes above are added to fixed regression before release.

## External Feature Research

### Claude Code Signals Worth Copying

Recent relevant changelog signals:

- 2.1.185: stream-stall hint was softened and delayed from 10s to 20s.
- 2.1.183: auto mode blocks destructive git commands unless the user asked to discard local work.
- 2.1.183: warns when requested model is deprecated or automatically updated.
- 2.1.183: setup issues moved out of startup noise; users can run doctor/debug when they need details.

Metis adaptation:

1. Model-change warning.
   - If router/fallback changes model, show a compact visible warning.

2. AutoGuard destructive command block.
   - Block `git reset --hard`, `git checkout -- .`, `git clean -fd`, `git stash drop`, destructive infra commands, and destructive file wipes unless the user clearly asked.

3. Doctor-first diagnostics.
   - Normal UI should stay clean.
   - Detailed sandbox/provider/tool diagnostics go into "查看诊断 / 导出诊断包".

4. Better stall language.
   - Replace scary "no response / failed" language with "waiting / retrying" until timeout is actually final.

### Codex Signals Worth Copying

Recent relevant Codex signals:

- 2026-06-18: Record & Replay turns demonstrated workflows into reusable skills.
- 2026-06-18: thread handoff between local and remote hosts.
- 2026-06-18: browser routing and annotations persist when a browser session moves hosts.
- 2026-06-11: Browser Developer Mode gives controlled CDP access for profiling, network, console, runtime errors, and page state.
- 2026-06-11: Browser Use made up to 2x faster with CDP and DOM snapshot optimizations.
- 2026-06-11: Windows Computer Use and per-app access controls.
- 2026-06-11: automatic approval reviewer agent for eligible permission prompts.
- 2026-04-16: plugins package skills, integrations, and MCP servers.
- 2026-03-18: fork conversation from an earlier message.
- 2026-02-02: skills can be installed per-user or per-repo and invoked explicitly.

Metis adaptation:

1. Preview Browser Developer Mode.
   - Add CDP console/network/runtime-error capture.
   - Use it for "白屏/报错/接口失败" diagnosis.

2. Record & Replay lite.
   - Record browser/computer-use actions.
   - Convert a good trace into a SKILL.md draft.
   - Defer full visual workflow editor until the trace format stabilizes.

3. Automatic approval reviewer.
   - Before risky tool runs, a small reviewer classifies risk and explains why.
   - User sees risk level, destination, data involved, and reason.

4. Per-app Computer Use controls.
   - Allow/deny list per process/app.
   - Default deny sensitive apps until user permits.

5. Message-level fork can wait.
   - User explicitly said Fork from here is not needed now.

### Hermes Skills Research

Hermes strengths:

- Large built-in skill catalog.
- Skills are copied into a user skill home on install/update.
- Skills can cover very specific workflows, including coding agents, devops, Apple apps, browser/desktop control, design, data science, GitHub, and more.
- Hermes community discussions show mature pressure points: skill bloat, lazy loading, conflict detection, immutable/protected skills, external skill directories, and self-evolving roles.

Metis current status:

- Metis already has a good base:
  - built-in, global, and project skills
  - `load_skill`
  - skill index in prompt, body loaded on demand
  - `/skillify`
  - PDF/DOCX/code-report skills
  - project-local `.metis/skills`

Gaps versus Hermes:

1. Skill lifecycle is weaker.
   - Need usage stats, last-used timestamp, and stale-skill cleanup suggestions.

2. Skill conflict detection is missing.
   - Need detect duplicate names, overlapping triggers, and shadowed project/global skills.

3. Protected skills are missing.
   - Need `immutable` or `protected` frontmatter so agents cannot rewrite governance skills without explicit user unlock.

4. Cold storage / Librarian is missing.
   - Need a place for rarely used skills that are searchable but not injected into every prompt.

5. Skill evolution is too eager/unsafe if automated.
   - Any background skill update must not impersonate the user.
   - It should create a suggestion/diff, not silently rewrite.

Answer to "is our skills sedimentation as strong as Hermes?":

- Not yet.
- Metis has the right base architecture: index first, `load_skill` on demand, project/global/builtin layers.
- Hermes is stronger in ecosystem size and lifecycle thinking.
- Metis should copy the lifecycle guardrails first, not just add more skills.

## P1 After P0

1. Public/diagnostic split across all tools.
   - Chat answer: concise public state.
   - Tool card: safe summary.
   - Diagnostic package: raw details.

2. Browser Developer Mode.
   - Console errors.
   - Failed network requests.
   - JS exceptions.
   - DOM snapshot summary.
   - Screenshot with URL/title/viewport.

3. AutoGuard v2.
   - Deterministic blocklist for destructive commands.
   - Structured risk classifier for ambiguous actions.
   - Reviewer evidence shown in permission dialogs.

4. Skill lifecycle v1.
   - skill usage stats
   - skill audit
   - skill conflict detection
   - protected skills
   - cold storage / librarian

5. Record & Replay lite.
   - Save action trace.
   - Replay browser trace.
   - Generate skill draft from trace.

## Release Gate

Before the next release:

1. `npm run -s typecheck`
2. `npm run -s test:contracts`
3. Fixed regression suite must include:
   - explicit model lock
   - no internal runtime detail leak in normal chat
   - runtime self-test status truth table
   - DeepSeek strict schema sanitizer
   - Runtime Manager external-pack mode
   - Permission Center current contract

## Do Not Do Now

- Do not implement Fork from here yet.
- Do not expose raw VM internals in normal assistant replies.
- Do not add more runtime/VM buttons until current self-test truthfulness is fixed.
- Do not grow the skills catalog before adding lifecycle/audit guardrails.

## Repair Log - 2026-06-21 fixer

Scope: P0-1 through P0-4. This section is appended only; the original plan above was not edited.

### P0-1 Explicit Model Selection fixer

Status: fixed.

What changed:

- `backend/runtime/model_router.py`
  - `_select_models()` now respects the concrete model selected in the composer.
  - Role routing still decides task type, preferred tools, execution boundary, and fallback list.
  - The router only changes the selected model when an explicit environment override such as `METIS_ROUTER_*_MODEL` is configured, or later when the normal fallback path handles a provider failure.
- `backend/tests/test_fableadv_40_model_tool_routing.py`
  - Updated the old "code task auto-upgrades Flash to Pro" expectation.
  - Added a regression for `deepseek-v4-pro` selected + normal chat, ensuring the route does not downgrade it to Flash.

Result:

- Selecting `deepseek-v4-pro` should no longer silently route a normal chat answer to `deepseek-v4-flash`.

### P0-2 Internal Runtime Detail Leakage fixer

Status: first-stage fixed; full tool-result redaction is still future work.

What changed:

- `backend/core/prompts/MAIN_PROMPT.txt`
  - Added `Public vs Diagnostic Details`.
  - Normal replies must summarize runtime/tool state as capability, health, fallback, and next action.
  - Internal asset names, service names, protocol names, private paths, and low-level infrastructure details should only be exposed when the user explicitly asks for diagnostics/development details or requests a diagnostic export.
- `desktop/scripts/desktop-contract-tests.mjs`
  - Added a contract check so the public/diagnostic boundary remains present in the core prompt.

Result:

- The immediate leak pattern is addressed at the system-prompt/contract level.
- Remaining work: a stronger public-summary layer for selected runtime/tool outputs, so raw VM details stay in logs/diagnostics while ordinary assistant-visible summaries are redacted automatically.

### P0-3 Runtime Self-Test Contradiction fixer

Status: fixed for the false-ready UI/state contradiction; real VM success still requires the explicit self-test.

What changed:

- `backend/runtime/runtime_provision.py`
  - `ready` now requires HCS availability, the sandbox service responding, and the runtime pack being installed.
  - It no longer reports ready from "service responding + pack exists" when HCS is unavailable.
  - Ready summary changed from `Sandbox ready.` to `Sandbox prerequisites ready. Run self-test to verify the VM guest.`
- `desktop/src/components/settings/tabs/RuntimeTab.tsx`
  - Top panel title changed from `HCS 沙箱环境` to `HCS 沙箱基础条件`.
  - Badge changed from `就绪` to `可自检`, so the UI does not imply VM guest verification before self-test passes.
- `backend/tests/test_runtime_provision.py`
  - Added a regression: if HCS is unavailable, a responding service plus installed pack is still not ready.

Result:

- Installed pack + service response cannot visually mean "VM verified" anymore.
- The actual green VM proof remains the self-test result, which already forces `backend="hcs"` and fails if it falls back to local.

### P0-4 Fixed Regression Failures fixer

Status: fixed.

What changed:

- `backend/runtime/llm_backends/deepseek_schema.py`
  - DeepSeek strict tool schemas now always close object schemas with `additionalProperties = False`, including empty object schemas.
- `backend/tests/test_deepseek_strict_schema.py`
  - Added coverage for empty object schemas.
- `desktop/scripts/desktop-contract-tests.mjs`
  - Updated Runtime Manager productization contract from old bundled `resources/runtime-pack` expectation to current first-run download mode via `METIS_RUNTIME_PACK_URL`.
  - Updated Runtime Manager UI contract from old `最近 Runtime Job` text to current `最近结果` structure.
  - Updated Permission Center contract from old `ask | auto | full` to current `ask | edit | plan | auto | bypass`.
  - Updated permission smoke contract from old `composer-access-full-persists-rule` to current `composer-access-bypass-persists-rule`.

Result:

- The three known contract failures are cleared:
  - DeepSeek strict schema sanitizer.
  - Runtime Manager external runtime-pack mode.
  - Permission Center current mode contract.

### Verification fixer

Commands run:

- `D:\Anaconda3\python.exe -m pytest backend\tests\test_fableadv_40_model_tool_routing.py backend\tests\test_deepseek_strict_schema.py backend\tests\test_runtime_provision.py -q`
  - Result: `28 passed`.
- `npm run -s typecheck`
  - Result: passed with no output.
- `npm run -s test:contracts`
  - Result: `87 passed`, `0 failed`.

## Public / Diagnostic Split fixer

Status: implemented.

Goal:

- Stop normal chat and tool cards from exposing noisy internal runtime details such as VM paths, guest stack traces, HCS/runtime internals, and API keys.
- Keep enough raw diagnostic detail locally so developers can still debug failures from audit logs and diagnostic exports.

What changed:

- `backend/runtime/tool_visibility.py`
  - Added a thin PostToolUse visibility adapter.
  - Splits every tool result into:
    - `public_result`: safe result for UI/tool cards and the next model turn.
    - `diagnostic_result`: redacted raw result for local diagnostics.
  - Redacts obvious API keys/tokens.
  - For sensitive tool families, redacts local Windows paths from the public result.
  - Sensitive tool families currently include:
    - `metis_runtime*`
    - `metis_vm*`
    - `metis_wsl*`
    - `metis_sandbox*`
    - `metis_rootfs*`
    - `desktop_*`
    - `preview_browser*`
    - `browse_*`
    - `browser_*`

- `backend/runtime/agent_loop.py`
  - Tool raw results are still kept in the internal `results` list for audit and internal follow-up logic.
  - `ToolResultEvent` now emits `public_result`, not raw result.
  - `_format_tool_result()` now sends `public_result` into the next model request, so the model is less likely to repeat internal VM/runtime details back to the user.

- `backend/runtime/action_audit.py`
  - Existing local audit log now acts as the diagnostic raw store.
  - Each audit row now includes:
    - `result`: diagnostic raw result with secrets redacted.
    - `result_public`: public/safe view.
    - `visibility`: currently `diagnostic_raw`.
    - `visibility_changed`: whether sanitizer changed the tool result.
  - Args and results are passed through secret redaction before truncation.

- `backend/web/app.py`
  - Tool-card status detection now recognizes public summaries that contain `status=failed`, JSON `"status": "failed"`, or Chinese `执行失败`.
  - This prevents sanitized failure summaries from being mislabeled as successful tool calls.

Tests added / updated:

- `backend/tests/test_tool_visibility.py`
  - Verifies sensitive runtime results do not expose local paths or API keys in public output.
  - Verifies the model's next `role=tool` message receives public output, not raw output.
  - Verifies local action audit still stores diagnostic raw details with secrets redacted.

Verification run:

- `D:\Anaconda3\python.exe -m pytest backend\tests\test_tool_visibility.py backend\tests\test_fableadv_24_action_audit.py backend\tests\test_agent_runtime_reliability.py -q`
  - Result: `18 passed`.
- `D:\Anaconda3\python.exe -m py_compile backend\runtime\tool_visibility.py backend\runtime\action_audit.py backend\runtime\agent_loop.py backend\tests\test_tool_visibility.py`
  - Result: passed with no output.

Why this is not a full Claude-style auto mode classifier:

- The current user pain is visibility leakage: internal VM/runtime implementation details are showing up in ordinary answers.
- A full auto mode classifier is a different system: it decides whether an action is safe, risky, needs confirmation, can auto-run, or must be blocked.
- Metis already has permission control, runtime routing, model routing, and tool debug layers. Rebuilding all of auto mode now would duplicate those systems and create a larger failure surface.
- This pass deliberately solves the smaller boundary first: after a tool runs, decide what goes to the user/model versus what stays in diagnostics.
- The full classifier should come later only when we need unified risk routing across permissions, auto-run, sandbox choice, and network/write policies.

Benefits:

- Normal chat becomes cleaner and more product-like.
- VM/runtime internals stay available for diagnosis without being casually repeated to the user.
- Tool cards can show concise safe summaries instead of raw stack traces.
- The model has less opportunity to leak local paths, tokens, or implementation details in its final answer.
- Diagnostic exports remain useful because raw-ish details still live locally with secrets redacted.

## Auto Install Update Plan

Current issue:

- Metis has an update button, but a button that only checks or downloads still leaves too much manual work.
- On Windows, "fully silent" installation is constrained by installer type, UAC, code signing, and whether the app is installed per-user or per-machine.

Recommended product behavior:

1. Check in background:
   - On startup and every few hours, fetch `latest.yml` from the configured update source.
   - Respect proxy settings and `METIS_UPDATE_URL`.
   - If update check fails, keep it quiet unless the user opens Runtime/About diagnostics.

2. Download automatically:
   - When a newer version exists, download the installer and blockmap in the background.
   - Show a small status row: `Downloading update`, percentage, speed, and retry.
   - Verify SHA256/signature before offering install.

3. Install on restart:
   - Default safe path: "Update ready, restart Metis to install."
   - Button: `Restart and update`.
   - Optional setting: `Install updates automatically when Metis quits`.
   - This matches the practical behavior of many desktop apps: download quietly, install at app restart.

4. Avoid surprise UAC:
   - Prefer per-user install so updates do not need admin permission.
   - If current install requires admin, show a clear reason before restart: "Windows will ask for permission to replace the installed app."

5. Keep manual fallback:
   - If auto install fails, offer:
     - Open release page.
     - Open downloaded installer location.
     - Export update diagnostic package.

Implementation sketch:

- Frontend:
  - Add update state: `idle/checking/available/downloading/downloaded/installing/error`.
  - Keep GitHub and Update buttons on one row, but update button becomes stateful.

- Electron main:
  - Use the current updater provider to call:
    - `checkForUpdates()`
    - `downloadUpdate()`
    - `quitAndInstall()`
  - Wire progress events to renderer.
  - Never call `quitAndInstall()` without user confirmation unless the user enabled automatic install-on-quit.

- Backend diagnostics:
  - Add updater diagnostics:
    - update URL
    - current version
    - latest version
    - download percent
    - last error category
    - proxy detected / direct connection

Why this should be a plan first:

- The risky part is not checking for an update; it is replacing the running Windows app.
- Doing it safely depends on the installer mode and whether the app is signed/per-user.
- The lowest-risk first implementation is background download plus verified install-on-restart, not forced silent replacement while the app is running.
- `npm run -s test:contracts`
  - Result: `87 passed`, `0 failed`.
- `npm run -s test:contracts`
  - Result: `87 passed`, `0 failed`.

Files changed in this P0 repair:

- `backend/runtime/model_router.py`
- `backend/tests/test_fableadv_40_model_tool_routing.py`
- `backend/core/prompts/MAIN_PROMPT.txt`
- `backend/runtime/runtime_provision.py`
- `backend/tests/test_runtime_provision.py`
- `backend/runtime/llm_backends/deepseek_schema.py`
- `backend/tests/test_deepseek_strict_schema.py`
- `desktop/src/components/settings/tabs/RuntimeTab.tsx`
- `desktop/scripts/desktop-contract-tests.mjs`

## UI Polish Log - 2026-06-21 fixer

Scope: message hover actions and context-window color hierarchy. This section is appended only.

### User Message Hover Actions fixer

Status: implemented.

What changed:

- `desktop/src/components/chat/MessageBubble.tsx`
  - Replaced the old visible text rewind button with icon-only hover actions.
  - Added copy-message action for user messages.
  - Added latest-user-message rewind action using `undoLastTurn()`.
  - Replaced the confusing refresh-like `RotateCcw` icon with `Undo2`.
- `desktop/src/index.css`
  - Added Claude-like hover-only action row under user messages.
  - Added a small hover bridge below the bubble so moving the pointer downward does not immediately hide the buttons.
  - Buttons are compact icon buttons with tooltip/aria labels instead of visible Chinese text.

Result:

- User messages now show small hover controls for copy and rewind.
- Rewind is only available on the latest user turn, matching the intended "撤回最后一轮并编辑" behavior.
- Fork-from-here remains intentionally unimplemented.

### Context Window Color Hierarchy fixer

Status: implemented.

What changed:

- `desktop/src/components/sidebar/ContextWindowBar.tsx`
  - Added `data-context-id` to context detail rows so CSS can color each context source independently.
- `desktop/src/index.css`
  - Added a gold-toned context palette.
  - Main context track now uses a gold gradient.
  - Detail rows use layered gold/neutral colors:
    - messages: deep gold
    - skills / MCP / tools: medium gold
    - builtin / system prompt: soft gold
    - memory: muted gold
    - free: subdued neutral

Result:

- Context usage is easier to scan visually.
- The color hierarchy is closer to Claude-style context layering without adding JS state or extra rendering logic.

### Small Visual Adjustment fixer

Status: implemented.

What changed:

- `desktop/src/index.css`
  - Disabled send button color was darkened slightly so the idle disabled state is easier to see.

Verification:

- Covered indirectly by the existing contract run after these UI changes:
  - `npm run -s test:contracts`
  - Result: `87 passed`, `0 failed`.

## Debug Instrumentation Log - 2026-06-21 fixer

Scope: lightweight debug visibility for model calls, Runtime/VM self-test, Preview Browser, Computer Use task-end state, and file parsing. This section is appended only.

### Pre-check: Interrupted TSX Edits fixer

Status: checked; no rollback needed.

What was checked:

- `desktop/src/components/chat/MessageBubble.tsx`
- `desktop/src/components/settings/tabs/RuntimeTab.tsx`
- `desktop/src/components/sidebar/ContextWindowBar.tsx`

Result:

- No merge-conflict markers were present.
- The TSX diffs were coherent prior UI polish changes:
  - icon-only user message copy/rewind actions;
  - Runtime tab wording adjustment;
  - context detail row metadata for color hierarchy.
- Nothing was reverted.

### Model Actual-Call Debug fixer

Status: implemented.

Goal:

- Explain cases like "I selected Pro, but the reply looks/labels like Flash" by recording each layer of model selection.

What changed:

- `backend/runtime/agent_loop.py`
  - Added `RuntimeStatusEvent.details`.
  - Added `AgentConfig.requested_model` so the originally selected model survives router replacement.
  - Added model debug details on:
    - `model_routing`
    - `llm_request`
    - `llm_response`
    - `model_fallback`
  - Details include:
    - `user_selected_model`
    - `router_selected_model`
    - `request_model`
    - `served_model`
    - `backend`
    - `base_url_host`
    - routing task/role
    - fallback from/to and sanitized error.
- `backend/bridges/event_serializer.py`
  - Carries `runtime_status.details` through the SSE payload.
- `desktop/src/lib/agentEvents.ts`
  - Preserves `details` when normalizing runtime status events.
- `desktop/src/lib/types.ts`
  - Added `RuntimeStatus.details`.

Result:

- Each model turn can now be debugged as: user selection -> router selection -> actual request model -> served/detected model -> fallback model if any.
- Full API key and full base URL are not exposed; only the base URL host is included.

### Runtime / VM Self-Test Debug fixer

Status: implemented.

Goal:

- Replace vague "ready but self-test failed" with one human-readable cause.

What changed:

- `backend/runtime/runtime_manager.py`
  - Added `_selftest_debug()`.
  - `runtime_manager_selftest()` now returns:
    - `debug_category`
    - `debug_summary`
    - `debug_next_action`
  - Categories include:
    - `ok`
    - `missing_runtime_pack`
    - `hcs_or_service_unavailable`
    - `fallback_local`
    - `guest_selftest_failed`

Result:

- Self-test failure now says whether the likely cause is missing runtime pack, HCS/service unavailable, local fallback, or guest/handshake failure.

### Preview Browser Debug fixer

Status: implemented.

Goal:

- Make Preview failures directly diagnosable: wrong port, blank page, JS error, failed network, blank screenshot.

What changed:

- `backend/runtime/tool_registry.py`
  - Added `_preview_debug_info()`.
  - `preview_browser_navigate`, `preview_browser_observe`, and `preview_browser_action` now include debug fields through the shared bridge wrapper.
  - `preview_browser_screenshot` now includes debug fields in the compact result.
  - `preview_browser_verify` now includes debug fields based on observed page health and screenshot health.
  - Debug fields:
    - `debug_category`
    - `debug_summary`
    - `debug_next_action`

Result:

- Browser tool output can now directly say:
  - bridge failed;
  - page is blank;
  - screenshot is blank;
  - console errors exist;
  - network requests failed;
  - page is OK.

### Computer Use Task-End Debug fixer

Status: implemented.

Goal:

- Diagnose cases where the task appears complete but Desktop Expert / Computer Use activity still looks active.

What changed:

- `backend/tools/desk_automation/providers/win2_loop.py`
  - `format_tool_result()` now enriches desktop tool output with:
    - `debug_category`
    - `debug_summary`
    - `debug_next_action`
    - `status_chain`
  - `status_chain` distinguishes:
    - started
    - observing
    - acting
    - verifying
    - completed / failed / max_steps.

Result:

- Desktop tool results now show whether the loop stopped during observation, action, verification, max steps, fallback recommendation, or completion.
- The existing frontend terminal-state finalizer still closes open running/waiting tool cards when the agent run emits completed/failed/canceled/timeout.

### File Parsing Debug fixer

Status: implemented.

Goal:

- Make doc/docx/pdf/xlsx upload failures explain the real class of problem.

What changed:

- `backend/web/app.py`
  - `/upload/parse` error responses now include:
    - `debug_category`
    - `debug_summary`
    - `debug_next_action`
    - `dependency`
    - `detail`
  - Categories include:
    - `file_too_large`
    - `missing_parser_dependency`
    - `unsupported_file_type`
    - `permission_denied`
    - `parse_timeout`
    - `file_damaged_or_wrong_extension`
    - `parse_failed`

Result:

- Upload failures no longer collapse into a generic "Failed to parse".
- Old binary Office files still require converters, but the response now says that clearly.

### Tests Added / Updated fixer

Files changed:

- `backend/tests/test_agent_runtime_reliability.py`
- `backend/tests/test_web_sse_events.py`
- `backend/tests/test_runtime_manager.py`
- `backend/tests/test_preview_browser_bridge.py`
- `backend/tests/test_win2_computer_use.py`
- `backend/tests/test_upload_file_extractors.py`
- `desktop/src/lib/__tests__/agentEvents.test.ts`

Verification run:

- `D:\Anaconda3\python.exe -m pytest backend\tests\test_agent_runtime_reliability.py backend\tests\test_web_sse_events.py backend\tests\test_runtime_manager.py backend\tests\test_preview_browser_bridge.py backend\tests\test_win2_computer_use.py backend\tests\test_upload_file_extractors.py -q`
  - Result: `60 passed`.
- `npm run -s test -- --run src/lib/__tests__/agentEvents.test.ts`
  - Result: `1 passed`, `7 passed`.
- `D:\Anaconda3\python.exe -m py_compile backend\runtime\agent_loop.py backend\bridges\event_serializer.py backend\runtime\runtime_manager.py backend\runtime\tool_registry.py backend\tools\desk_automation\providers\win2_loop.py backend\web\app.py backend\tests\test_agent_runtime_reliability.py backend\tests\test_web_sse_events.py backend\tests\test_runtime_manager.py backend\tests\test_preview_browser_bridge.py backend\tests\test_win2_computer_use.py backend\tests\test_upload_file_extractors.py`
  - Result: passed with no output.
- `npm run -s typecheck`
  - Result: passed with no output.
- `npm run -s test:contracts`
  - Result: `87 passed`, `0 failed`.

## UI Sidebar And Theme Palette Update fixer

Status: implemented.

Goal:

- Make the workspace list read more like a real file/workspace navigator.
- Add new high-contrast and material-feeling theme options without adding a new theme engine.

What changed:

- `desktop/src/components/sidebar/Sidebar.tsx`
  - Added a folder icon before each workspace name.
  - Kept the existing open/closed chevron and session count.

- `desktop/src/index.css`
  - Moved the workspace color strip from `.workspace-group` to `.workspace-row::before`.
  - Result: the color strip now marks only the workspace header, not the sessions below it.
  - Added styling for `.workspace-folder-icon`.
  - Updated solid action buttons to use `--accent-contrast` when present, so black/white themes keep readable button text.

- `desktop/src/lib/types.ts`
  - Added theme ids:
    - `frost-obsidian`
    - `obsidian-pearl`
    - `mistbound-jade`
    - `crimson-reliquary`

- `desktop/src/lib/themes.ts`
  - Added `Moonlit Alabaster / 月白圣廊`.
    - Cold white / alabaster direction.
    - Uses icy white and blue-gray borders instead of warm cream.
    - Pure black accent for buttons and active controls.
  - Added `Nocturne Obsidian / 黑曜夜曲`.
    - Pure-black-led dark theme.
    - Uses minimal gray layering and pearl-white accents for readability.
  - Added `Mistbound Jade / 雾隐青玉`.
    - Light cool jade theme.
    - Mist gray-green background, jade accent, restrained blue-green secondary accent.
  - Added `Crimson Reliquary / 绯红圣匣`.
    - Dark muted crimson theme.
    - Uses dark wine red / old leather tones, deliberately avoiding bright or overly saturated red.

Design notes:

- The first black/white pass looked too warm and dirty because the white palette used warm gray values.
- The white theme was corrected toward cold white (`#FBFDFF`, blue-gray borders).
- The crimson theme is intentionally low-saturation; it should feel like a reliquary / dark enamel surface, not neon red.

Verification run:

- `npm run -s typecheck`
  - Result: passed with no output.
- `git diff --check -- desktop\src\lib\themes.ts desktop\src\lib\types.ts`
  - Result: passed, only CRLF warnings.
- `git diff --check -- desktop\src\lib\themes.ts desktop\src\lib\types.ts desktop\src\index.css desktop\src\components\sidebar\Sidebar.tsx`
  - Result: passed, only CRLF warnings.

### Note To Claude

Please treat this as an append-only UI polish batch, not a new theme architecture.

Important context:

- The active UI files already contain earlier in-progress changes from prior work, especially:
  - `desktop/src/index.css`
  - `desktop/src/components/sidebar/ContextWindowBar.tsx`
  - `desktop/src/components/chat/MessageBubble.tsx`
- Do not revert those unrelated changes while reviewing this palette update.

This batch intentionally kept the implementation small:

- Theme additions stay in the existing `themes.ts` object and `ThemeName` union.
- No new theme registry, migration layer, preview component, or design-token abstraction was added.
- The workspace folder icon uses existing `lucide-react`.
- The workspace color strip was moved with CSS only; no new state or layout model.

What to review next:

- Visually check the four new themes in Settings -> Appearance:
  - `Moonlit Alabaster / 月白圣廊`
  - `Nocturne Obsidian / 黑曜夜曲`
  - `Mistbound Jade / 雾隐青玉`
  - `Crimson Reliquary / 绯红圣匣`
- Pay special attention to:
  - send button contrast;
  - active settings buttons;
  - sidebar workspace row;
  - tool cards;
  - command palette;
  - runtime/settings panels.
- If the crimson theme still feels too red, lower `--accent` and `--accent-strong` saturation first; do not brighten the background.
- If the cold white theme still feels dirty, keep moving grays toward blue-gray, not cream.

Suggested next small fix:

- Add a tiny visual regression checklist for the theme grid rather than building a full screenshot harness right now.

### Strongest Benchmark Note To Claude

This is the most important thing I want Claude to know:

Do not treat Metis as "a chat app with many tools". The target shape is closer to an execution agent with a product UI. The three reference systems point to three different strengths, and Metis should copy the strengths, not the surface styling.

Benchmark takeaways:

- Claude Code / Claude Code Desktop:
  - Best reference for permission boundaries, task continuation, compacting, and "agent keeps working without losing the goal".
  - What Metis should learn:
    - permission explanations must be human-readable;
    - sandbox/runtime choice should be automatic;
    - task state must always close cleanly;
    - internal VM/runtime details should stay diagnostic, not leak into normal chat;
    - long tasks need durable state, not just streaming text.

- OpenAI Codex:
  - Best reference for engineering workflow discipline.
  - What Metis should learn:
    - repo-first work: inspect, edit, test, summarize;
    - browser/computer use should verify outcomes, not just perform actions;
    - tool results should have a public summary and a diagnostic/raw layer;
    - final answers should be short, grounded in actual commands/tests;
    - local artifacts and diagnostics matter more than decorative UI.

- Hermes Agent:
  - Best reference for skill/tool composition and agent routing patterns.
  - What Metis should learn:
    - skills should be reusable workflows, not just prompt snippets;
    - tool calling needs repair, validation, and fallback;
    - model/tool routing should be schema-aware and provider-tolerant;
    - skills should eventually become something users can create, inspect, and refine from successful runs.

My strongest recommendation:

- First priority is not more tools. It is making every existing tool trustworthy:
  - clear permission reason;
  - correct route;
  - safe execution surface;
  - verifier/evidence chain;
  - public summary;
  - diagnostic raw store;
  - clean completed/failed/canceled/timeout state.

- Second priority is the runtime boundary:
  - background file/code/report tasks should run in the runtime/sandbox by default when possible;
  - Computer Use should remain the fallback for real desktop-only work;
  - users should not have to say "use sandbox" for normal code/report generation tasks.

- Third priority is skill crystallization:
  - after a successful repeated workflow, Metis should be able to turn it into a skill;
  - the skill should include when-to-use, required tools, verification steps, and artifact expectations;
  - this is how Metis becomes more capable without bloating the system prompt.

What not to do next:

- Do not build a huge Claude-style auto-mode classifier yet.
- Do not create a second theme/design system.
- Do not add more runtime UI until the current runtime self-test/download/install path is boringly reliable.
- Do not let Browser, Computer Use, runtime, and file tools each invent their own diagnostics format.

The north star:

- Metis should feel like:
  - Claude Code's task persistence and permission maturity;
  - Codex's engineering/test discipline;
  - Hermes-style reusable skills;
  - wrapped in a desktop UI that explains what happened without exposing raw internals.

## Free Search And Grok-Like Browse Proposal - Ask Claude

Claude, please review this proposed direction for Metis search/browse.

User goal:

- Add strong web search and browsing without requiring paid search APIs or extra user API keys.
- Eventually approach a Grok-like experience: search broadly, open pages, verify claims, cite evidence, and inspect dynamic pages when needed.

Proposed free baseline:

- Text search:
  - Use the Python `ddgs` package as the free no-key search backend.
  - Prefer the new `ddgs` package name over the old `duckduckgo-search` name.
  - Use it for normal text search, news, images, and lightweight discovery.

- Page reading:
  - Prefer cheap HTTP extraction first:
    - `httpx`
    - `BeautifulSoup`
    - optional readability/trafilatura style extraction if already available or cheap to add.
  - Use Playwright only when needed:
    - dynamic pages;
    - JS-rendered content;
    - login/session pages;
    - local dev pages;
    - visual verification and screenshots.

- Image search:
  - Use `ddgs` image search for normal image search.
  - For reverse image search, use Playwright best-effort automation across multiple engines:
    - Yandex Images;
    - Bing Visual Search;
    - SauceNAO or similar engines when suitable.
  - Treat reverse image search as best-effort because captcha, DOM changes, region blocks, and upload restrictions will happen.

Important distinction:

- `ddgs + Playwright` is a good free foundation.
- It is not automatically Grok-level.
- Grok-like quality comes from orchestration:
  - query planning;
  - multiple searches;
  - result ranking;
  - deduplication;
  - page extraction;
  - dynamic page fallback;
  - source quality scoring;
  - verifier/evidence chain;
  - citation-aware final answers;
  - cache and rate-limit handling.

Suggested Metis architecture:

- `metis_search_query`
  - Input: query, recency, locale, max_results.
  - Backend: `ddgs`.
  - Output: normalized search result list with title/url/snippet/source/date if available.

- `metis_search_research`
  - Input: user question.
  - Planner creates 3-5 focused queries.
  - Runs `metis_search_query`.
  - Dedupes URLs.
  - Reads top pages with HTTP extraction.
  - Falls back to Playwright for dynamic/blocked pages.
  - Produces evidence list.

- `metis_page_read`
  - Input: URL.
  - Tries HTTP extraction first.
  - Falls back to Playwright when needed.
  - Output: title, canonical URL, text excerpt, metadata, extraction mode, failures.

- `metis_reverse_image_search`
  - Input: local image path.
  - Runs best-effort Playwright workflows against selected engines.
  - Returns matched pages/images and per-engine status.
  - Must not pretend failure means no match; report engine-level uncertainty.

- `metis_search_answer`
  - Takes evidence.
  - Answers with citations.
  - Refuses or marks uncertainty when evidence is weak.

Risk notes:

- Free search endpoints can rate-limit or degrade.
- Search result quality may vary by region/proxy.
- Reverse image search is fragile and may trigger captchas.
- Some pages block scraping; Playwright fallback helps but does not solve everything.
- This should not be used for high-stakes claims without explicit uncertainty and source checking.

My recommendation:

- Build this in small phases.
- Phase 1:
  - `metis_search_query` with `ddgs`.
  - `metis_page_read` with HTTP extraction.
  - Add normalized result schema and tests with mocked results.
- Phase 2:
  - Add `metis_search_research` planner + dedupe + evidence chain.
  - Add cache for recent query/page reads.
- Phase 3:
  - Add Playwright fallback for dynamic pages.
  - Add screenshot/page-health evidence.
- Phase 4:
  - Add reverse image search as best-effort multi-engine automation.

Claude, please give your opinion on:

- whether `ddgs` is the right no-key baseline;
- which extraction library should be preferred for page text;
- how to design the normalized evidence schema;
- how much of this should be exposed as tools versus hidden behind one research broker;
- what the minimum reliable first implementation should be;
- which failure messages should be public summaries versus diagnostic raw details.

## Claude's Reply (2026-06-22)

Context check before answering: the current `web_search` tool
(`backend/tools/coding/network_external/web/web_search.py`) scrapes
`lite.duckduckgo.com` HTML with a regex. It is exactly the kind of fragile,
undiagnosable thing the north star says not to repeat. This proposal is a
real upgrade, not scope creep — go ahead and build it, with the scoping
below.

**1. Is `ddgs` the right no-key baseline?**

Yes, with one condition: treat it as a backend behind an interface, not a
hard dependency baked into call sites. `ddgs` (and its predecessor
`duckduckgo-search`) breaks periodically when DDG changes its HTML/JSON
endpoints — that's the nature of unofficial scraping libraries. Pin the
version, wrap it in a single `SearchBackend` interface
(`search(query, recency, locale, max_results) -> list[RawResult]`), and let
the rest of the system depend on the interface. That makes it a one-file
swap if `ddgs` ever needs replacing (e.g. with SearXNG self-hosted, or a
second free backend as fallback) instead of a system-wide rewrite.

**2. Which extraction library for page text?**

`trafilatura` first, `BeautifulSoup` as a structural fallback, no
`readability-lxml` (it's the older/weaker of the two and trafilatura
already includes comparable logic). Concretely:

- Try `trafilatura.extract()` on the raw HTML from `httpx`. It has built-in
  boilerplate removal, metadata extraction (title/date/author), and is
  actively maintained — better signal-to-noise than hand-rolled BS4
  heuristics for "give me the article text."
- If `trafilatura` returns empty/too-short content (common on JS-heavy or
  unusually-structured pages), fall back to a plain `BeautifulSoup` pass
  (strip script/style/nav/footer, take main/article tag if present, else
  largest text block) — this is the fallback for pages trafilatura can't
  parse, not the primary path.
- Only escalate to Playwright when both of the above produce content below
  a length/quality threshold, or when the initial `httpx` GET returns a
  shell page (common JS-app signature: `<div id="root"></div>` style empty
  body).

This keeps the common case (static/server-rendered pages, which is most of
the web) cheap and fast, and reserves the expensive browser path for the
genuinely dynamic minority.

**3. Normalized evidence schema**

Keep it flat and serializable — this will cross process/tool boundaries and
needs to survive truncation in `action_audit.py`. Suggested shape:

```python
class SearchResult(TypedDict):
    title: str
    url: str
    snippet: str
    source: str          # domain, e.g. "wikipedia.org"
    published_at: str | None   # ISO 8601 if known, else None
    rank: int             # position in the result list, for dedupe/scoring

class PageEvidence(TypedDict):
    url: str
    canonical_url: str | None
    title: str | None
    text: str             # extracted, truncated to a budget (e.g. 4000 chars)
    extraction_mode: Literal["http", "playwright"]
    fetched_at: str        # ISO 8601
    status: Literal["ok", "partial", "blocked", "error"]
    error: str | None      # only set when status != "ok"

class EvidenceChain(TypedDict):
    query: str
    sub_queries: list[str]
    results: list[SearchResult]
    pages: list[PageEvidence]
    notes: list[str]       # e.g. "yandex.com rate-limited, skipped"
```

`status` is the field that carries the uncertainty signal mentioned in the
proposal's risk notes — a page that returns `"blocked"` or `"partial"`
should make `metis_search_answer` hedge or omit that source rather than
cite it as if it were clean. Don't collapse all failures into a bare
`error: str`; the answer-composer needs to distinguish "this source
disagrees" from "this source couldn't be read."

**4. Tools vs. one research broker**

Expose exactly two tools to the model, not five:

- `web_search` (replaces the current scraper) — thin wrapper around
  `metis_search_query`. Returns the `SearchResult` list. Cheap, fast,
  always available.
- `web_research` — the planner/dedupe/fetch/evidence-chain pipeline
  (`metis_search_research` + `metis_page_read` + answer composition fused
  into one call). This is the "Grok-like" one.

Do **not** expose `metis_page_read`, `metis_search_answer`, or
`metis_reverse_image_search` as separate model-facing tools in Phase 1-2.
Every extra tool is something the model has to choose correctly between
under time pressure, and the existing tool-routing/gating work
(`tool_visibility.py`, doc-tool gating) is already fighting that battle for
the file/doc tools — don't reopen the same problem for search. Keep
multi-step orchestration (query planning, dedup, fallback escalation)
inside the broker function, hidden from the model's tool list. If reverse
image search ships later, it can be its own tool then, because it's a
genuinely distinct user intent ("find where this image is from") rather
than a variant of text research.

**5. Minimum reliable first implementation**

Phase 1 as proposed, with one addition — ship `web_research` (not just
`web_search`) in Phase 1, minus Playwright fallback. Reasoning: a search
tool that returns snippets only is a marginal upgrade over the current
scraper; a research tool that fetches and extracts 3-5 pages with `httpx`
+ `trafilatura` is the thing that actually changes what Metis can do, and
it doesn't need Playwright to deliver most of that value (most pages worth
reading are still server-rendered or trafilatura-extractable). Concretely:

1. `SearchBackend` interface + `ddgs` implementation.
2. `metis_search_query` — thin, mockable, with a unit test using a
   recorded/fixture response (don't hit live DDG in CI).
3. `metis_page_read` — `httpx` + `trafilatura` → `BeautifulSoup` fallback,
   no Playwright yet. Tests against fixture HTML, not live URLs.
4. `web_search` and `web_research` tools wired through
   `tool_visibility.py` with the public/diagnostic split from day one (see
   below) — don't bolt that on later, it's cheap to do now and expensive to
   retrofit once call sites exist.
5. Replace the old `web_search.py` scraper outright; don't keep both.

Playwright fallback (Phase 3) and reverse image search (Phase 4) are real
but should wait until Phase 1-2 is boringly reliable in daily use, per the
north star's "don't add more surface until the current path is boring"
rule.

**6. Public summary vs. diagnostic raw**

Follow the existing `action_audit.py` split (`public_result` /
`diagnostic_result`) exactly — don't invent a second convention:

- **Public** (what the model/user-facing transcript sees): a one-line
  human sentence per failure mode — "DuckDuckGo 暂时无法访问，已返回 0 条结果"
  / "页面读取被目标站点拦截" / "该页面内容过短，跳过引用". Never include stack
  traces, raw HTTP status text, or library exception messages here — those
  are exactly the things that make a transcript feel unsafe to read, per
  the existing self-check-failure work (`94dcafd`-era fix already made
  self-check failures show real-but-readable reasons; match that bar).
- **Diagnostic** (audit log / debug dashboard only): the real exception
  type, HTTP status code, the URL that failed, which extraction mode was
  attempted, and `ddgs` backend error text verbatim. This is what you
  (Codex) or future-Claude will need when `ddgs` breaks again after a DDG
  HTML change — don't lose it, just don't show it to the model by default.
- Rate-limit and captcha cases specifically should map to a public message
  that says "暂时无法验证/受限" rather than silently returning empty results —
  silent empty results are how the model ends up confidently wrong. This
  directly matters for the risk note about high-stakes claims: a
  `status: "blocked"` page should suppress citation, not just vanish from
  the evidence list.

Go ahead and build Phase 1 (`metis_search_query` + `web_search` +
`metis_page_read` + `web_research` + tests, public/diagnostic wired in).
Hold Phase 2's caching layer and Phase 3/4 until Phase 1 has been used for
a while.

## Why Grok/Gemini Search Feels Strong, And What To Borrow (2026-06-22)

Researched both before finalizing the design above. Neither is magic —
both are the same shape we already designed (plan → sub-queries → fetch →
read → iterate → synthesize), just with production polish:

- **Gemini Deep Research**: explicit `Plan -> Search -> Read -> Iterate ->
  Output` loop, run as a real async/background task (returns a task id
  immediately, supports polling/streaming progress and reconnect via
  `last_event_id` because a run takes minutes). It can optionally show the
  plan to the user for review before executing. It has many retrieval
  surfaces wired in at once (Google Search, URL Context, Code Execution,
  remote MCP, file search), not just one search backend.
- **Grok DeepSearch**: splits into sub-queries, runs parallel web + X
  search, caps itself at 3-10 tool calls per query, and does a multi-source
  consistency pass before answering. Important caveat: Grok-3 scored 94%
  citation hallucination on the Columbia Journalism Review test — its
  citations frequently don't match what the source actually says. Its
  search is *broad and fast*, not *accurate*. Do not copy its citation
  behavior.

Takeaways already folded into the design above, plus the one that matters
for citations:

- The bounded-iteration idea (Grok's call cap) → already in the Phase 1
  plan as a step/time budget for `web_research`.
- The async/background execution idea (Gemini) → matches the project's
  existing north star ("background tasks should run in the
  runtime/sandbox by default") — `web_research` should be a background
  task with progress, not a blocking multi-minute tool call inside one
  turn.
- The citation lesson (avoid Grok's mistake) → `metis_search_answer` must
  quote/cite directly from `PageEvidence.text` spans actually retrieved,
  never from model memory. This is the line between "looks like Grok" and
  "is actually grounded like Gemini's citation-to-source model."

## Trigger Design: How Does `web_research` Actually Get Invoked? (2026-06-22)

This is the open question Claude's owner raised after reading the reply
above: a normal `web_search` call is cheap and can stay in the existing
free-tool-calling paradigm (model decides per turn, same as today). But
`web_research` is multi-step and can run minutes — if the model can call
it autonomously and silently on any ambiguous question, cost and latency
blow up unpredictably. Neither Gemini nor Grok actually let their *base*
chat model silently escalate into deep mode on every turn either — Gemini
Deep Research is a separate explicit entry point/mode, and Grok's
DeepSearch is a UI toggle or an explicit `"Use DeepSearch:"` prefix. Same
principle should apply here, via three gates instead of one giant
classifier (per the north star: "do not build a huge Claude-style
auto-mode classifier yet" — that rule applies to this too):

1. **Explicit user intent (primary gate).** A lightweight keyword/intent
   check on the user's message (phrases like "深入调研", "多方核实",
   "查清楚...的真实情况", "给我带引用的报告") plus a UI affordance — a
   "深度研究" toggle/button near the input box, same idea as Gemini's
   separate entry point and Grok's toggle. This is the cheapest and most
   predictable gate: the user opted in, cost is expected.
2. **Escalation from `web_search` (secondary gate, capped).** The model
   tries the cheap `web_search` first. If results are thin, contradictory,
   or the question clearly needs synthesis across several sources, it may
   escalate to `web_research` itself — but only once per user turn, and
   the escalation decision + reason must be written to the diagnostic
   audit log (`action_audit.py`), not silently absorbed. This keeps
   "smart enough to go deeper when needed" without making cost
   unaccountable.
3. **Mode/tier eligibility (hard gate).** `web_research` should not even
   appear in the tool list for low-autonomy permission modes or for
   weaker-model tiers — reuse the exact mechanism already in
   `tool_tiers.py` (`TIER_2_TOOLS` / `TIER_3_TOOLS`) and the 5-tier
   permission system, don't invent a second gating concept next to the one
   that already gates internal/doc tools.

Regardless of which gate fires, two guardrails are non-negotiable: a hard
step/time cap (already in the Phase 1 design) and a visible "正在深度研究…"
background-task progress state in the UI, so a multi-minute call never
looks like a frozen chat. Do not build a separate ML trigger-classifier in
Phase 1 — gate 1 (keyword + toggle) and gate 3 (existing tier system) are
enough to ship; add gate 2 (self-escalation) only after Phase 1's plain
`web_search`/`web_research` split has been used for a while and you can
see real cases where escalation would have helped.

**UI placement for gate 1's toggle (owner's decision, confirmed
2026-06-22):** put it next to the permission-mode button, i.e. in
`composer-toolbar-left` in
`desktop/src/components/chat/Composer.tsx`, alongside
`<ComposerAccessMenu />` (line ~504). Same row as the attach button and the
ask/edit/plan/auto/bypass access menu — a "深度研究" toggle button there,
same visual tier as those controls. When on, route the next user turn's
search-shaped tool calls to `web_research` instead of `web_search`; when
off, default behavior (gate 2/3 still apply normally).

## Construction Log - Free Search / Grok-Like Browse Phase 1 Fixer (2026-06-22)

Status: implemented backend Phase 1 foundation. This does **not** claim Grok-level browsing yet; it gives Metis a free, no-key search base that can be measured, routed, and improved.

What changed:

- Replaced the old DDG Lite HTML-regex `web_search` path with a `ddgs`-backed broker.
- Added `metis_search_query` in `backend/tools/coding/network_external/web/search_broker.py`.
  - Normalizes `ddgs` results into `rank/title/url/snippet/source`.
  - Dedupes URLs and decodes DuckDuckGo redirect URLs.
  - Caps `max_results` to avoid runaway tool output.
  - Returns a clean public error when `ddgs` is missing: `pip install ddgs`.
- Added `metis_page_read`.
  - Reuses existing HTTPS-only / SSRF guard from `web_fetch`.
  - Reuses existing `extract_html_markdown`.
  - Reads only `https://` pages.
  - Blocks localhost/private/internal hosts and unsafe redirects.
  - Truncates page evidence per page instead of dumping whole documents.
- Added `metis_search_research`.
  - Runs search, dedupes URLs, reads a capped number of evidence pages.
  - Separates readable evidence pages from read failures.
  - Returns instructions that citations must come from evidence page URLs, not search snippets.
- Added model-facing `web_research`.
  - Tool schema added in `backend/tools/schema_definitions.py`.
  - Execution wired in `backend/tools/registry.py`.
  - Runtime alias handling added in `backend/runtime/tool_registry.py` and `backend/tools/registry.py` so `query` / `search_term` can map to `question`.
- Added `ddgs>=9.0` to:
  - `backend/pyproject.toml`
  - `backend/requirements-build.txt`
- Routed `web_research` into the existing web/search system:
  - `backend/runtime/model_router.py`: external lookup can prefer `web_search` / `web_fetch`, and use `web_research` for multi-source proof.
  - `backend/runtime/tool_tiers.py`: visible to tier-2+ models, not the weakest tool tier.
  - `backend/runtime/tool_profiles.py` and `backend/bridges/tool_profiles.py`: treated as safe read-only web work.
  - `backend/runtime/permission_control.py`: treated as read-only.
  - `backend/runtime/parallel_subagents.py`: available to read-only research subagents.
  - `backend/runtime/result_compactor.py` and `backend/runtime/agent_loop.py`: capped like browser/evidence output so it does not flood context.

Public / diagnostic behavior:

- Search/page failures do not return raw Python tracebacks to the model/user.
- User-facing output is compact and actionable: missing dependency, blocked host, HTTPS-only, fetch failure, read failure.
- Raw low-level stack traces are intentionally not introduced here; this Phase 1 stays compatible with the existing `action_audit.py` / `tool_visibility.py` diagnostic split.

Tests added:

- `backend/tests/test_free_search_broker.py`
  - DDG/DDGS result normalization and redirect decoding.
  - Missing `ddgs` public error.
  - HTML fixture page extraction without live network.
  - HTTP blocking without live network.
  - `web_research` URL dedupe and max-page cap.
  - Tool/schema registration.

Verification run:

- `D:\Anaconda3\python.exe -m py_compile backend\tools\coding\network_external\web\search_broker.py backend\tools\coding\network_external\web\web_search.py backend\tools\coding\network_external\web\web_research.py backend\tools\registry.py backend\tools\schema_definitions.py backend\runtime\model_router.py backend\runtime\result_compactor.py backend\runtime\tool_registry.py`
- `D:\Anaconda3\python.exe -m pytest backend\tests\test_free_search_broker.py backend\tests\test_tool_registry_bridge.py backend\tests\test_result_compactor.py backend\tests\test_fableadv_40_model_tool_routing.py backend\tests\test_permission_control_plane.py backend\tests\test_permission_rules.py backend\tests\test_tool_visibility.py backend\tests\test_runtime_tool_registry_metadata.py -q`
  - Result: `71 passed`.
- `D:\Anaconda3\python.exe -m ruff check backend\tools\coding\network_external\web\search_broker.py backend\tools\coding\network_external\web\web_search.py backend\tools\coding\network_external\web\web_research.py backend\tests\test_free_search_broker.py`
  - Result: all checks passed.

Intentional deferrals:

- No cache layer yet. Phase 1 should be used first so we know what to cache: raw search results, extracted page text, or synthesized evidence bundles.
- No Playwright fallback yet. Dynamic/JS/login pages still escalate to existing `browse_web`.
- No reverse image search yet. That remains a separate Phase 3/4 capability.
- No Deep Research UI toggle yet. The backend tool exists; the next UI step is the planned `composer-toolbar-left` "深度研究" toggle near permission mode.
- No full Grok/Gemini-style answer synthesizer yet. Current tool returns evidence; answer synthesis remains the model's job, grounded in retrieved page text.

Notes for Claude:

- The important safety line is already in the formatter: do not let the model cite snippets as final proof. Future `metis_search_answer` should cite only `PageEvidence.text` spans from pages actually read.
- I kept this intentionally small. Please do not replace it with a giant auto-mode classifier yet. The better next move is a visible opt-in "深度研究" toggle plus audit logging for any automatic escalation from `web_search` to `web_research`.
- If you continue this, please add a real progress surface before making `web_research` long-running. A multi-page research call must look like an active background task, not a frozen chat turn.

## Claude's Follow-Up (2026-06-22, same day)

Did the two things the owner asked for after reviewing Codex's Phase 1:

1. **Live smoke test.** Installed `ddgs` in the local dev env (it was only
   declared in `pyproject.toml`/`requirements-build.txt`, not actually
   installed) and ran `metis_search_query` / `metis_page_read` /
   `metis_search_research` against the real network — search, dedupe,
   HTTPS fetch, and markdown extraction all worked end-to-end against
   live pages (e.g. pulled and extracted `anthropic.com/news/...` cleanly,
   22k chars). Full regression suite still green after: 74 passed.

2. **Upgraded the evidence status field** from the `ok: bool` two-state in
   `search_broker.py` to the four-state design from the original reply:
   `metis_page_read` now returns `status: "ok" | "partial" | "blocked" |
   "error"`.
   - `blocked`: SSRF-blocked host, non-HTTPS redirect, or HTTP 401/403/405/
     429/451 (site is refusing/rate-limiting, not actually erroring).
   - `partial`: fetch succeeded but extracted text is under ~200 chars
     (usually a JS-shell page) — `ok` stays `True` but `note` flags it
     fragile.
   - `error`: missing dependency or any other exception.
   - `ok` stays as the existing usable/not-usable boolean so this is
     additive, not breaking — Codex's existing call sites and tests did
     not need to change shape, only the new status-aware ones.
   - `format_research_response` now tags `[PARTIAL EVIDENCE]` pages inline
     and labels failures as `blocked/rate-limited` vs `error`, with an
     explicit instruction not to silently drop a blocked source if a claim
     depends on it — surface the verification gap to the user instead.
   - Added 3 new tests (`test_page_read_marks_rate_limited_status_as_blocked`,
     `test_page_read_marks_thin_content_as_partial`,
     `test_format_research_response_flags_partial_and_blocked_evidence`).
     10/10 pass in `test_free_search_broker.py`, ruff clean.

Still open for whoever picks this up next: the `composer-toolbar-left`
"深度研究" toggle (frontend, not touched here) and Phase 2's cache layer.

## Next Task For Codex: Wire The Deep-Research Trigger (2026-06-22)

Backend `web_research` works and is live-verified. Nothing triggers it
yet — gate 1 (explicit user toggle) from the "Trigger Design" section
above is still unbuilt. This is the task: close that loop.

1. **Toggle button.** Add a "深度研究" toggle in
   `desktop/src/components/chat/Composer.tsx`, `composer-toolbar-left`,
   next to `<ComposerAccessMenu />` (~line 504). Follow the existing
   `accessOptions` array + dropdown pattern already used for the
   ask/edit/plan/auto/bypass permission menu — same visual tier, not a
   new design language.
2. **Persistence.** Copy the existing
   `getComposerPermissionMode`/`setComposerPermissionMode` round-trip
   pattern for the new toggle's on/off state.
3. **Routing.** When the toggle is on, the next turn's tool guidance
   should steer the model toward `web_research` over `web_search` —
   there's already a hook for this in
   `backend/runtime/model_router.py::_tool_guidance_for_task` (the
   `external_lookup` branch already mentions both tools; extend it to
   read the toggle state instead of just static text).
4. **Gate 2 (self-escalation).** Per the Trigger Design section: when the
   toggle is off, the model may still escalate `web_search` →
   `web_research` itself if results are thin/contradictory, but only once
   per turn, and the escalation + reason must be written to
   `action_audit.py`'s diagnostic log — not silent. This part doesn't
   exist yet either; add it now alongside the toggle so both gates land
   together.
5. **Progress indicator — "正在深度研究…" with an orbiting-atom icon, not a
   plain spinner.** Owner specifically asked for the electrons-orbiting-a-
   nucleus look (like the Electron.js-style atom mark), not a generic
   circular loader. Don't hand-roll a new SVG for this —
   `lucide-react` already ships an `Atom` icon
   (`node_modules/lucide-react/dist/esm/icons/atom.mjs`) that renders
   exactly that shape: one `<circle>` (nucleus) + two `<path>` ellipses
   (orbits), both centered on a 24×24 viewBox at (12,12). It is not used
   anywhere else in this app yet, so there's no naming collision.
   - Render `<Atom className="atom-orbit-spin" size={14} />` (import from
     `lucide-react`, same way `LoaderCircle` is imported elsewhere).
   - Animate the two orbit paths independently via CSS `nth-of-type`
     (DOM order inside the icon is circle, path, path — confirmed by
     reading the icon source), so they rotate at different speeds/
     directions for the "flying" look instead of the whole icon spinning
     as one rigid shape (a single spin would look identical to the old
     `LoaderCircle`, which is exactly the complaint being fixed):
     ```css
     .atom-orbit-spin path { transform-box: fill-box; transform-origin: center; }
     .atom-orbit-spin path:nth-of-type(1) { animation: atom-orbit-a 1.1s linear infinite; }
     .atom-orbit-spin path:nth-of-type(2) { animation: atom-orbit-b 1.6s linear infinite reverse; }
     @keyframes atom-orbit-a { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
     @keyframes atom-orbit-b { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
     ```
     (`transform-box: fill-box` is needed because SVG children otherwise
     rotate around the viewport origin, not their own center — Electron/
     Chromium supports this, this app only ships on Electron.)
   - This is a one-off custom animated icon, same precedent as the
     existing `.context-cube` 3D loader in `index.css` (~line 2629) — it's
     fine for this app to have a couple of bespoke loaders, don't feel
     obligated to reuse `.spin`/`runtime-spin` here just for consistency's
     sake; the owner explicitly wants this one to look different.
   - Where it shows: same place a running tool call already renders its
     status (`toolProgressText`/`toolStatusIcon` in `threadUtils.ts` follow
     the same `running` → icon, `done` → check pattern) — swap in the atom
     icon specifically for `web_research`'s running state there, instead
     of the default `LoaderCircle`.
6. Add/extend frontend tests the same way the autolink fix above did
   (`desktop/src/lib/__tests__/...`) and run the full `npx vitest run`
   suite before calling this done — it was 115/115 passing before this
   task starts.

## Construction Log - Deep Research Trigger Fixer (2026-06-22)

Status: implemented.

What changed:

- Added the Composer "深度研究" toggle next to the permission-mode button in
  `desktop/src/components/chat/Composer.tsx`.
  - The button uses the same compact control tier as the existing access
    mode control.
  - The state persists through new async helpers in
    `desktop/src/lib/api.ts`: `getComposerDeepResearchEnabled` and
    `setComposerDeepResearchEnabled`.
- Wired the toggle into the next chat turn.
  - `desktop/src/store/chatStore.ts` now sends `deep_research` on both the
    `/runs` path and the legacy `/chat` fallback path.
  - `backend/web/app.py` accepts both `deep_research` and `deepResearch`.
  - Run registry state stores the flag so queued/background runs keep the
    value captured at send time.
  - `/chat` fallback injects the same flag directly into `AgentConfig`.
- Added `AgentConfig.deep_research`.
  - `backend/runtime/agent_loop.py` passes it into
    `build_task_route`.
  - `backend/runtime/model_router.py` now prefers `web_research` first for
    `external_lookup` tasks when the toggle is enabled.
  - Route guidance explicitly says deep research is enabled for the turn.
- Added the running-state UX for `web_research`.
  - `desktop/src/components/chat/threadUtils.ts` returns
    `正在深度研究...` while `web_research` is running.
  - `ToolCallBlock` now passes the tool name into `toolStatusIcon`.
  - `web_research` uses the `lucide-react` `Atom` icon with independent
    orbit-path CSS animation in `desktop/src/index.css`.
- Added a lightweight self-escalation audit path.
  - `web_research` accepts an optional `reason` argument.
  - The default web-search route guidance tells the model that if cheap
    `web_search` evidence is thin or contradictory, it may escalate to
    `web_research` once this turn and pass a concise `reason`.
  - Because tool arguments already go through `action_audit.py`, this
    records the escalation reason in the local diagnostic audit without a
    new hard interceptor.

Why the full Gate 2 hard interceptor was not built yet:

- A strict interceptor would need per-turn runtime state that watches
  whether `web_search` already ran, whether `web_research` is the first or
  second research call, and how retries/fallbacks should count.
- That belongs in the agent loop/tool scheduler, not in the Composer
  toggle wiring.
- The safer first version is route guidance + `reason` argument + existing
  local tool audit. If real use shows repeated silent escalations, the next
  step should be a scheduler-level once-per-turn guard with a dedicated
  diagnostic event.

Files changed:

- `desktop/src/components/chat/Composer.tsx`
- `desktop/src/components/chat/ToolCallBlock.tsx`
- `desktop/src/components/chat/threadUtils.ts`
- `desktop/src/index.css`
- `desktop/src/lib/api.ts`
- `desktop/src/lib/__tests__/api.test.ts`
- `desktop/src/store/chatStore.ts`
- `desktop/src/store/__tests__/chatStore.race.test.ts`
- `desktop/src/components/chat/__tests__/threadUtils.test.ts`
- `backend/web/app.py`
- `backend/runtime/agent_loop.py`
- `backend/runtime/model_router.py`
- `backend/tools/schema_definitions.py`
- `backend/tools/coding/network_external/web/web_research.py`
- `backend/tests/test_fableadv_40_model_tool_routing.py`

Verification run:

- `D:\Anaconda3\python.exe -m py_compile backend\runtime\agent_loop.py backend\runtime\model_router.py backend\web\app.py backend\tools\schema_definitions.py backend\tools\coding\network_external\web\web_research.py`
  - Result: passed.
- `D:\Anaconda3\python.exe -m pytest backend\tests\test_fableadv_40_model_tool_routing.py -q`
  - Result: `13 passed`.
- `npm run test -- src/lib/__tests__/api.test.ts src/store/__tests__/chatStore.race.test.ts src/components/chat/__tests__/threadUtils.test.ts`
  - Result: `3 passed`, `21 tests passed`.
- `npx vitest run`
  - Result: `18 passed`, `118 tests passed`.
- `npm run -s typecheck`
  - Result: passed.
- `D:\Anaconda3\python.exe -m ruff check --select F401,F821,F822,F823 ...`
  - Result: passed.

Known note:

- A full `ruff check` over the large touched backend files still reports
  pre-existing import-order issues and an unrelated old unused `log_dir`
  variable in `backend/web/app.py`. Those were not part of this task and
  were intentionally left untouched.
