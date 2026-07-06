# Metis Core Stabilization 06 - Local VM Runtime

> 编写日期：2026-07-06
> 目标：把 `local_vm` 第一版收敛为可验收、可维护的 MetisRuntime WSL runner。

---

## 0. 结论

当前 Metis 不做 cloud 端执行。`local_vm` 的第一版可用路径是本机 `MetisRuntime` WSL distro：

- Code 文件修改仍走 `local_worktree`。
- 命令执行、测试、构建可以选择 `execution_profile=local_vm`。
- `local_vm` 后端固定走 `metis_wsl`。
- `metis_wsl` 会启动 WSL2 utility VM，但它不是 Metis HCS direct runner。
- HCS direct 仍是单独的实验路径，不参与 Code/Cowork/UI 主路径。

---

## 1. 执行边界

| 层 | 责任 |
|---|---|
| `/runs` | Code + `local_vm` 仍创建 managed worktree |
| Agent loop | 只把 `execute_bash_command` / `run_tests` 送进 `local_vm` |
| 文件写入工具 | 继续写入 Code worktree，不进入 VM runner |
| `local_vm_runner` | command-only runner，固定 `backend=metis_wsl` |
| `runtime_job` | 创建 isolated runtime copy，导出 stdout、patch、artifact |
| Artifact registry | 注册 runtime artifact 和 diff，不把 registry 当真实来源之外的 cache |

---

## 2. 工具链验收

脚本：

```powershell
python -m backend.runtime.vm_toolchain_check --root D:\pycharm\py.project\Miro
```

必须在 `MetisRuntime` 内检查：

- `python3`
- `pip`
- `uv`
- `node`
- `npm`
- `pnpm`
- `git`
- `rg`
- `curl`
- `tar`
- `unzip`
- `zstd`
- `build-essential` (`gcc`, `g++`, `make`)

验收规则：

- 缺工具时修 rootfs 或 runtime bundle。
- 不在 host 主路径里做 “缺 VM 工具就用宿主机工具” 的 fallback。
- 不把 HCS direct 当成 `local_vm` 的默认候选。
- 验收报告写入 `$METIS_RUNTIME_ARTIFACTS_DIR/metis_vm_toolchain_check.tsv`。

---

## 3. Rootfs 补包位置

优先修改：

- `backend/runtime/guest/Dockerfile.rootfs.rich`
- `backend/runtime/guest/build_rich_rootfs.sh`
- `desktop/scripts/build-rich-rootfs.ps1`

不要修改：

- `/runs` 主路径
- `agent_loop` 的工具选择逻辑
- React UI 状态机
- HCS direct runner

---

## 4. Smoke

真实 smoke 应验证：

- Code run 使用 `execution_profile=local_vm` 时仍创建 worktree。
- `local_vm` 能进入 `MetisRuntime`。
- `python3 -c "print('METIS_VM_OK')"` 能返回 stdout。
- 命令能在 isolated workspace copy 里写文件。
- patch 能导出 changed files。
- artifact 能从 `$METIS_RUNTIME_ARTIFACTS_DIR` 回到 registry。
- 结果中的 runner/backend 是 `local_vm` / `metis_wsl`。
- 不启动或选择 HCS backend。

---

## 5. Cowork 接入

Cowork 第一版只做本地协调：

- `plan -> subruns -> diff/artifact summary`
- parent `/runs` 不创建全局 worktree；每个 subrun 创建自己的 managed worktree
- plan 使用 bounded LLM planner，输出 `metis.cowork_plan.v2`
- 每个 subrun 必须包含 `objective`、`inputs`、`expected_artifacts`、`acceptance_criteria`、`execution_profile`、`dependencies`
- subrun 默认 `local_worktree`
- 当 parent run 选择 `local_vm` 时，planner 可让测试/构建类 subrun 选择 `local_vm` command runner
- `local_vm` subrun 仍在 worktree 副本上执行，runner/backend 必须是 `local_vm` / `metis_wsl`
- 后端发稳定 `subrun_*` lifecycle：`subrun_planned -> subrun_running -> subrun_succeeded | subrun_failed | subrun_canceled`
- 每个 terminal subrun 必须产出 `metis.cowork_subrun_evidence.v1`：成功需要 diff、真实 artifact 或 stdout/test evidence；失败/取消需要 failure reason。没有 evidence 的 subrun 由后端强制转成 `subrun_failed`，不算完成。
- summary 注册为 `metis.artifact.v1` report，subrun report/diff 一并汇总
- UI 只展示任务拆分、状态、diff、artifact
- 不做 cloud worker，不做复杂远程调度

Code 边界：

- Code run 默认 `execution_profile=local_worktree`，因此默认创建 managed worktree，文件编辑和 diff review 都在 worktree 中发生。
- `execution_profile=local_vm` 不是让 Code UI 或文件编辑进 VM；它只让 `execute_bash_command` / `run_tests` 这类命令工具通过 MetisRuntime WSL 执行，workspace 仍是 Code worktree 副本。
- 在 `local_worktree` Code run 内，单次 `execute_bash_command` / `run_tests` 可传 `execution_profile="local_vm"` 或 `use_local_vm=true`，用于测试、构建、危险 shell 的隔离执行。
- 不传命令级 selector 时，命令仍在当前 worktree 内按原权限协议执行。

Promote review flow：

- Promote 不再是直接全量 apply。UI 必须先展示 diff/review，再让用户选择文件。
- 后端 review schema 为 `metis.worktree_promote_review.v1`，包含 `files`、`stat`、`patch`、`can_apply`、`conflicts`。
- `paths` 为空表示 review/apply 全量 diff；传入 `paths` 表示选择性 promote。
- 冲突解释由后端生成，至少包含冲突文件、source workspace 当前状态和原始 `git apply --check` 输出。
- apply 成功后写入 promotion record 和 rollback patch，记录在 worktree registry metadata。
- rollback 使用 `git apply -R` 回滚指定 promotion；如果 source workspace 后续又被改动，rollback 也必须先 check 并返回 conflicts。

Planner 边界：

- LLM planner 只允许输出 JSON，不允许工具调用。
- `max_subruns` 被后端限制在 1-6。
- 后端生成 `subrun_id`，不信任模型生成的身份。
- `dependencies` 只能引用已经存在的更早 subrun。
- planner 失败、坏 JSON、字段缺失或越界时，后端回退 deterministic planner，但仍填齐 v2 必填字段。

第一版实现入口：

- `backend.runtime.cowork_coordinator.iter_local_cowork_events`
- `/runs` 中 `surface_mode=cowork` 分流到本地 coordinator
- Flask smoke 锁定 subrun event、summary artifact、`local_vm` 使用 `metis_wsl` 而不是 HCS
