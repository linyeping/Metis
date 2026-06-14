# OmniParser 接入说明（RTX 4060 / CUDA）

本项目的 **SoM 解析** 在 `screen_parser.parse_roi_l2` 中完成。

- **`backend`: `omniparser`** — 仅 OmniParser（专用图标 + Florence 描述）。
- **`backend`: `hybrid` 或 `both`（推荐）** — **原 YOLO+OCR 与 OmniParser 同时跑**，再按 `hybrid_cross_iou` 合并重叠框、拼接 `content`，互为补充。
- **`backend`: `yolo_ocr`** — 仅原先管线（默认）。

---

## 1. 克隆仓库

推荐放在 **`mine/miro/var/rely/`** 下（与项目路径约定一致，见 `mine/miro/var/README.md`）：

```powershell
cd <repo-root>\miro\var\rely
git clone https://github.com/microsoft/OmniParser.git
cd OmniParser
```

记下该路径，例如：**`<repo-root>\miro\var\rely\OmniParser`**（包含 `util/`、`gradio_demo.py` 的仓库根）。配置里 `omni_parser_root` 写此路径即可（JSON 里可用正斜杠写法，见下文）。

---

## 2. 安装 PyTorch（CUDA 版，给 RTX 4060 用）

**4060 为 Ada 架构，需 CUDA 12.x 的 PyTorch 轮子。** 建议在 **Python 3.12** 的 conda 环境中操作（与 OmniParser README 一致）。

```powershell
conda create -n miro_gui python=3.12 -y
conda activate miro_gui
```

安装带 CUDA 12.4 的 PyTorch（以 [pytorch.org](https://pytorch.org/get-started/locally/) 为准；若链接变更请按官网命令替换）：

```powershell
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

验证 GPU：

```powershell
python -c "import torch; print('cuda:', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
```

应输出 `cuda: True` 与 `NVIDIA GeForce RTX 4060`（或类似名称）。

---

## 3. 安装 OmniParser 其余依赖

在 **OmniParser 仓库根目录**：

```powershell
cd <repo-root>\miro\var\rely\OmniParser
pip install -r requirements.txt
```

若 `pip` 又把 `torch` 装成 CPU 版，请先固定好 cu124 的 torch，再对冲突包单独安装；或先 `pip install -r requirements.txt`，再**重装**一遍 cu124 的 `torch torchvision`。

---

## 4. 从 Hugging Face 下载权重（V2）

**模型集合（组织/仓库名）：** `microsoft/OmniParser-v2.0`  
页面：<https://huggingface.co/microsoft/OmniParser-v2.0>

需要落到 **OmniParser 仓库内的 `weights/`** 目录，目录结构如下（官方 README 与之一致）：

```
OmniParser/
  weights/
    icon_detect/
      model.pt
      model.yaml
      train_args.yaml
    icon_caption_florence/          ← 注意：下载下来的目录常叫 icon_caption，需改名
      config.json
      generation_config.json
      model.safetensors
      ...（仓库里列出的其余文件）
```

**推荐：使用 Hugging Face CLI**（需已 `pip install huggingface_hub` 并可选 `huggingface-cli login`）：

```powershell
cd <repo-root>\miro\var\rely\OmniParser

huggingface-cli download microsoft/OmniParser-v2.0 icon_detect/model.pt --local-dir weights
huggingface-cli download microsoft/OmniParser-v2.0 icon_detect/model.yaml --local-dir weights
huggingface-cli download microsoft/OmniParser-v2.0 icon_detect/train_args.yaml --local-dir weights
huggingface-cli download microsoft/OmniParser-v2.0 icon_caption/config.json --local-dir weights
huggingface-cli download microsoft/OmniParser-v2.0 icon_caption/generation_config.json --local-dir weights
huggingface-cli download microsoft/OmniParser-v2.0 icon_caption/model.safetensors --local-dir weights
```

若官方还列出 `preprocessor_config.json`、`tokenizer` 等文件，请一并下载到同一 `weights/icon_caption/` 下。

**重命名（必须）：**

```powershell
# 若本地是 weights/icon_caption，改名为 icon_caption_florence
Rename-Item -Path weights\icon_caption -NewName icon_caption_florence
```

若 README 要求执行 `python weights/convert_safetensor_to_pt.py`，请在仓库内按说明执行。

---

## 5. 与 desk_automation 共用同一 Python 环境

**OmniParser 与 Miro desk_automation 必须在同一解释器里**（因为 `vision_loop` 进程内会直接 `import util.utils`）。

做法：在已装好 OmniParser 依赖的 conda 环境中，再安装 desk_automation 所需包（或反过来在你现有 Agent 环境中按上面步骤装 torch + OmniParser 依赖）。

---

## 6. 启用 OmniParser（配置）

编辑 `%USERPROFILE%\.miro\desk_automation.json`，增加或合并 `som_parser` 段（路径改成你的克隆路径）：

```json
{
  "som_parser": {
    "backend": "hybrid",
    "omni_parser_root": "C:/Users/Serein/Desktop/HAPPY/agent/mine/miro/var/rely/OmniParser",
    "omni_box_threshold": 0.05,
    "omni_iou_threshold": 0.7,
    "omni_imgsz": 640,
    "omni_batch_size": 12,
    "omni_use_paddleocr": true,
    "omni_use_local_semantics": true,
    "omni_fallback_yolo": true,
    "hybrid_cross_iou": 0.45
  }
}
```

| 字段 | 说明 |
|------|------|
| `backend` | `hybrid` / `both` 双路融合；`omniparser` 仅 Omni；`yolo_ocr` 仅旧方案。 |
| `hybrid_cross_iou` | 两路框 IOU≥该值视为同一控件，合并并集与 `content`（默认 0.45）。 |
| `omni_parser_root` | OmniParser **仓库根目录**；优先于环境变量 `OMNI_PARSER_ROOT`。 |
| `omni_batch_size` | **8GB 显存建议 8～16**；OOM 时减小或设 `omni_use_local_semantics`: false（图标无 Florence 描述，更轻）。 |
| `omni_fallback_yolo` | `true` 时 OmniParser 异常则自动回退 YOLO+OCR。 |

保存后**重启**运行 desk_automation / vision 的进程。

---

## 7. 代码入口（已实现）

- 桥接与缓存：`orchestrator/omniparser_bridge.py`（进程内**只加载一次**检测模型与 Florence）。
- 路由：`orchestrator/screen_parser.py` 中 `parse_roi_l2` 根据 `get_som_parser_policy()` 选择后端；`vision_loop` **无需再改**，仍调用 `parse_roi_l2`。
- SoM 图仍用本项目的 `_draw_som`，保证 **~1、~2…** 与多模态提示一致。

---

## 8. 显存与性能（4060 8GB）

- 首帧加载 Florence + 检测会占显存较多；`omni_batch_size` 从 **12** 降到 **8** 可缓解。
- 仍 OOM 时：将 `omni_use_local_semantics` 设为 `false`（失去图标自然语言描述，但框一般仍在）。

---

## 参考链接

- GitHub：<https://github.com/microsoft/OmniParser>
- 权重：<https://huggingface.co/microsoft/OmniParser-v2.0>
- 论文：<https://arxiv.org/abs/2408.00203>
