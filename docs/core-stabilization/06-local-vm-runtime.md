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
- subrun 默认 `local_worktree`
- 需要执行测试/构建的 subrun 可选择 `local_vm` command runner
- `local_vm` subrun 仍在 worktree 副本上执行，runner/backend 必须是 `local_vm` / `metis_wsl`
- 后端发 `subagent_start -> subagent_progress -> subagent_done`
- summary 注册为 `metis.artifact.v1` report，subrun report/diff 一并汇总
- UI 只展示任务拆分、状态、diff、artifact
- 不做 cloud worker，不做复杂远程调度

第一版实现入口：

- `backend.runtime.cowork_coordinator.iter_local_cowork_events`
- `/runs` 中 `surface_mode=cowork` 分流到本地 coordinator
- Flask smoke 锁定 subrun event、summary artifact、`local_vm` 使用 `metis_wsl` 而不是 HCS
