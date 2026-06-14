# desk_automation — 参考实现（嵌套 tools）

位于 **`mine/miro/Tools/desk_automation/`**，属于 3.28 真源体系。  
融合进产品时对应 **`miro/tools/desk_automation/`**（见 `方案/架构/DIRECTORY_TREE.txt`）。

## 启动方式（统一入口）

**推荐**：在 `mine/miro` 下 `py web/app.py` → 打开 `http://127.0.0.1:5000`。  
前端为 L'Atelier 风格统一页（聊天 + 视觉操控 + 步骤编排 + 工具 + 委派 + 设置），desk API 通过 `web/desk_blueprint.py` Flask Blueprint 挂到同一进程，不再需要单独启动 httpd。

独立 httpd（`python -m backend.tools.desk_automation`、端口 8765）仍保留可用，但通常不需要。

若页面显示旧版：关掉残留 python 进程 → 重启 → Ctrl+F5。

## 能力

| 模块 | 说明 |
|------|------|
| `inventory/scan_software.py` | Windows 注册表枚举已安装程序（**总开关关也可用**） |
| `inventory/scan_cli.py` | 探测 PATH 内常见 CLI |
| `inventory/scan_windows.py` | 可见窗口列表、进程 Top-N、开始菜单快捷方式 |
| `inventory/scan_env.py` | 关键路径 + 环境变量快照（隐藏敏感 key） |
| `capture/screenshot.py` | 主显示器 PNG（需 **总开关开** + 未暂停） |
| `capture/window_shot.py` | 按窗口标题关键字截图 |
| `input/actions.py` | 点击、打字、单键（需 **总开关开** + 未暂停） |
| `input/file_ops.py` | `prepare_for_upload`（复制到 ~/.miro/tmp/）、`open_in_explorer` |
| `orchestrator/vision_loop.py` | **★ 核心**：`auto` / `human` / `skill` 三模式主循环 |
| `orchestrator/frame_diff.py` | 帧差分析：节流判断、变化热点 ROI |
| `orchestrator/ocr_locate.py` | **human**：**双引擎 OCR**（Tesseract + PaddleOCR）自动探测、优先级可配、关键词匹配定位（省多模态费用）。参考 `translation/` 实践 |
| `orchestrator/skill_followup.py` | 画面稳定后日志提示可挂接 OpenClaw 技能链 |
| `orchestrator/screen_reader.py` | 多模态 LLM 解析动作（Ollama/百炼/Gemini/OpenAI） |
| `orchestrator/nlu.py` | NLU：意图分类+上下文编译（参考 mine/miro） |
| `orchestrator/task_state.py` | 目标/任务持久化状态机（JSON 文件 `~/.miro/desk_tasks.json`） |
| `orchestrator/goal_runner.py` | 步骤编排运行器：逐步执行、暂停等待、异常处理 |
| `orchestrator/cursor_bridge.py` | Cursor 自动化桥接：窗口聚焦、提示词注入、等待完成 |
| `orchestrator/ai_bridge.py` | 多 AI 委派：模板组装、附件准备、剪贴板/文件输出、路由建议 |
| `server/httpd.py` | 本机 HTTP API（**33 端点**）+ 静态页 `static/control.html` |
| `hooks/esc_listener.py` | 可选：全局 ESC → 暂停（需 pynput） |

## Python 环境

本机有两个 Python 环境：

| 环境 | 路径 | pytesseract | paddleocr |
|------|------|-------------|-----------|
| VS 自带 | `D:\DevelopTools\VisualStudio\Shared\Python39_64\python.exe` | 未装 | 未装 |
| **HAPPY conda** | `D:\Users\Serein\anaconda3\envs\HAPPY\python.exe` | **已装** | **已装** |

`ocr_locate.py` 会 **自动探测** HAPPY 环境：如果当前 Python 缺少 pytesseract / paddleocr，
会自动将 HAPPY 环境的 `site-packages` 注入 `sys.path` 来复用已安装的包，**无需重复安装**。
PaddleOCR 已知的 protobuf 版本冲突也会自动处理。

可通过 `GET /api/ocr/status` 查看探测结果（包含 `python_env` 字段）。

## 安装（可选）

```bash
cd <agent 根目录>
# 推荐用 HAPPY 环境（已有 pytesseract + paddleocr）
D:\Users\Serein\anaconda3\envs\HAPPY\python.exe -m pip install -r mine/miro/Tools/desk_automation/requirements-optional.txt
# 或用当前环境（自动注入 HAPPY 的包也可以）
pip install -r mine/miro/Tools/desk_automation/requirements-optional.txt
```

## 启动控制面

```bash
cd <agent 根目录>/mine/miro
# 推荐：用 HAPPY 环境直接启动（所有 OCR 包已就绪）
D:\Users\Serein\anaconda3\envs\HAPPY\python.exe -m Tools.desk_automation
# 或：用任意 Python 启动（会自动注入 HAPPY 的 site-packages）
python -m backend.tools.desk_automation
```

浏览器打开 **`http://127.0.0.1:8765/`**（端口可在 `~/.miro/desk_automation.json` 的 `http_port` 修改）。

- **总开关**默认关；网页勾选后才允许截图/键鼠。  
- **暂停**：网页按钮或 `POST /api/pause`；与 **ESC 钩子**（另开终端跑 `esc_listener`）共用同一标志文件 `~/.miro/desk_automation.pause`。

## HTTP API（仅 127.0.0.1）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | `enabled` / `paused` |
| POST | `/api/enabled` | `{"enabled": true}` |
| POST | `/api/pause` / `/api/resume` | 暂停 / 继续 |
| GET | `/api/inventory/software` | 已装软件 JSON |
| GET | `/api/inventory/cli` | CLI 探测 |
| GET | `/api/inventory/env` | 路径/环境变量快照 |
| GET | `/api/inventory/windows` | 可见窗口列表 |
| GET | `/api/inventory/processes` | 进程 Top-N |
| GET | `/api/inventory/shortcuts` | 开始菜单快捷方式 |
| GET | `/api/monitors` | 显示器信息（不要求开关） |
| GET | `/api/screenshot.png` | PNG（要开关） |
| POST | `/api/capture/window` | `{"title":"关键字"}` → 窗口截图 PNG |
| POST | `/api/input/click` | `{"x":100,"y":200,"button":"left","clicks":1}` |
| POST | `/api/input/type` | `{"text":"hello"}` |
| POST | `/api/input/key` | `{"key":"enter"}` |
| POST | `/api/file/prepare` | `{"paths":[...]}` → 复制到 ~/.miro/tmp/ |
| POST | `/api/file/explore` | `{"path":"..."}` → 打开文件管理器定位 |
| POST | `/api/goal/start` | `{"goal":"…","steps":[…]}` → 启动编排目标 |
| POST | `/api/goal/stop` | 停止当前目标 |
| GET | `/api/goal/state` | 目标完整状态（含步骤列表） |
| GET | `/api/goal/log?n=50` | 最近 N 条日志 |
| POST | `/api/goal/step` | `{"action":"cursor:…"}` 追加步骤 |
| POST | `/api/goal/finish` | `{"success":true}` 标记完成 |
| GET | `/api/cursor/status` | 检测 Cursor 窗口是否存在 |
| GET | `/api/routing?type=…` | 任务类型 → AI 路由建议 |
| POST | `/api/delegate/clipboard` | `{"prompt":"…"}` → 复制到剪贴板 |
| POST | `/api/delegate/compose` | 按模板 A/B 组装提示词 |
| POST | `/api/exec_mode` | `{"mode":"auto"}` 或 `human` / `skill`（旧名 `program`→`skill`） |
| POST | `/api/vision/start` | `{"goal":"…","exec_mode":"…","max_steps":50}` |
| GET | `/api/vision/state` | 含 `stable_frame_streak`、`idle_continue_count`（human 空转计数）等 |
| GET | `/api/human/policy` | 返回 `human_policy` 合并后的节流参数 |
| GET | `/api/ocr/status` | 双引擎（Tesseract + PaddleOCR）探测结果、prefer 配置、可用性 |

## 三种 exec_mode

| 模式 | 含义 |
|------|------|
| **auto** | 先 **skill**（`programmatic.py`），覆盖不了再 **human** 智能链 |
| **human** | OCR → 帧差节流（流式小变不刷 API）→ 全图大变用全屏 API，否则热点 ROI 拼接后调 API |
| **skill** | 仅程序化/OpenClaw 式技能链，不跑 human（旧配置 `program` 已迁移为此名） |

`~/.miro/desk_automation.json` 可写 **`human_policy`** 覆盖默认节流阈值（见 `config.get_human_policy()`）；含 **`max_idle_continues`**、**`step_warn_ratio`**（v10.14 human 防死转 / 步数预警）。

## OpenClaw 对照

- **`skills/peekaboo`**：macOS 专用 CLI 自动化；本包为 **Windows 友好** 的 Python 侧实现思路。  
- **`skills/coding-agent`**：委派外部编码 CLI；**`desk_delegate` Skill** 负责「指挥 + 本机 GUI」层，与本 tools 配合。

## 与 Cursor / 多模型

行为规则写在 **`方案/架构/skills_preview/desk_delegate/SKILL.md`**。

## human 智能链（优先省 API 费）

1. **本地 OCR — 双引擎**（Tesseract + PaddleOCR，参考 `translation/` 实践）：从目标句拆关键词，匹配屏上文字框 → 点击中心，**不调多模态**。
   - **Tesseract**：自动探测 `E:\apps\AOCR\tesseract.exe` + pytesseract（HAPPY 环境已装）
   - **PaddleOCR**：HAPPY 环境已装（参考 `translation/中译英/1.py`），中文识别更强
   - 环境自动注入：即使用 VS 默认 Python 启动，也能自动找到 HAPPY 环境的包
   - 优先级：`~/.miro/desk_automation.json` → `ocr.prefer` = `"tesseract"`(默认) / `"paddle"` / `"both"`
   - 两个都找不到 → 跳过 OCR 步骤，直接走多模态 API。
2. **帧差**：与上一帧对比；若全图变化很小且距上次 API 未过 `min_api_interval_sec`，或「全图 diff 低但局部格子能量高」（流式输出），则 **节流跳过本轮多模态**。
3. **多模态**：全图 `diff_ratio` 高 → 全屏送 API；否则若有热点 → **只送 ROI 拼接图**（省 token）。
4. 连续多帧被节流后，日志提示可挂接 **skill**（browser / coding-agent 等，由宿主接）。

## 编排引擎（步骤）

**步骤编排**：`cursor:` / `ask:` / `shell:` / `screenshot` / `wait:N` / `verify` — 见 `goal_runner.py`。

**HTML**：五标签页——视觉操控 / 步骤编排 / 工具环境 / AI 委派 / 设置。
