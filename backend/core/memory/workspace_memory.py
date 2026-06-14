"""WorkspaceMemory — 项目级持久记忆，跨会话保留。

存储在 ``<workspace>/.metis/memory.json``，包含：
- 项目类型推断 (Python / TypeScript / mixed)
- 关键文件路径
- 架构笔记（agent 总结的认知）
- 常用命令
- 学习到的模式

记忆有 7 天过期策略：``architecture_notes`` 超过 7 天未更新时，
prompt 注入时会自动忽略以防止过时信息误导。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


_MEMORY_DIR = ".metis"
_MEMORY_FILE = "memory.json"
_NOTES_EXPIRY_DAYS = 7
_MAX_KEY_FILES = 15
_MAX_COMMON_COMMANDS = 10
_MAX_LEARNED_PATTERNS = 10


@dataclass
class WorkspaceMemory:
    """项目级持久记忆，跨会话保留。"""

    workspace_root: str
    project_type: str = ""
    key_files: List[str] = field(default_factory=list)
    architecture_notes: str = ""
    common_commands: List[str] = field(default_factory=list)
    learned_patterns: List[str] = field(default_factory=list)
    last_updated: float = 0.0

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def save(self) -> None:
        """保存到 ``<workspace>/.metis/memory.json``。"""
        path = Path(self.workspace_root) / _MEMORY_DIR / _MEMORY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        self.last_updated = time.time()
        data = asdict(self)
        # workspace_root 不存入 JSON（恢复时由调用方提供）
        data.pop("workspace_root", None)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        # 确保 .metis 目录有 .gitignore
        _ensure_gitignore(Path(self.workspace_root) / _MEMORY_DIR)

    @classmethod
    def load(cls, workspace_root: str) -> "WorkspaceMemory":
        """从磁盘加载，不存在则返回空实例。"""
        path = Path(workspace_root) / _MEMORY_DIR / _MEMORY_FILE
        if not path.exists():
            return cls(workspace_root=workspace_root)
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            known_fields = {f for f in cls.__dataclass_fields__}
            filtered = {k: v for k, v in data.items() if k in known_fields}
            return cls(workspace_root=workspace_root, **filtered)
        except Exception:
            return cls(workspace_root=workspace_root)

    # ------------------------------------------------------------------
    # 更新接口
    # ------------------------------------------------------------------

    def set_project_type(self, project_type: str) -> None:
        if project_type and project_type != self.project_type:
            self.project_type = project_type

    def add_key_file(self, path: str) -> None:
        if path and path not in self.key_files:
            self.key_files.append(path)
            self.key_files = self.key_files[-_MAX_KEY_FILES:]

    def add_common_command(self, command: str) -> None:
        cmd = command.strip()
        if not cmd:
            return
        # 去重：如果已存在则移到末尾
        if cmd in self.common_commands:
            self.common_commands.remove(cmd)
        self.common_commands.append(cmd)
        self.common_commands = self.common_commands[-_MAX_COMMON_COMMANDS:]

    def add_learned_pattern(self, pattern: str) -> None:
        pat = pattern.strip()
        if pat and pat not in self.learned_patterns:
            self.learned_patterns.append(pat)
            self.learned_patterns = self.learned_patterns[-_MAX_LEARNED_PATTERNS:]

    def update_architecture_notes(self, notes: str) -> None:
        if notes and notes.strip():
            self.architecture_notes = notes.strip()

    # ------------------------------------------------------------------
    # Prompt 注入
    # ------------------------------------------------------------------

    def to_prompt_block(self) -> str:
        """生成适合注入系统提示的文本块。

        架构笔记超过 7 天未更新时自动忽略。
        """
        if not self._has_useful_content():
            return ""

        lines = ["\n\n---\n[Project Memory — from previous sessions]"]

        if self.project_type:
            lines.append(f"Project type: {self.project_type}")

        if self.key_files:
            display_files = self.key_files[:10]
            lines.append(f"Key files: {', '.join(display_files)}")

        # 架构笔记：检查过期
        if self.architecture_notes and not self._notes_expired():
            # 截断到 ~500 chars 以控制 token
            notes = self.architecture_notes
            if len(notes) > 500:
                notes = notes[:500].rstrip() + "..."
            lines.append(f"Architecture: {notes}")

        if self.common_commands:
            display_cmds = self.common_commands[:5]
            lines.append(f"Common commands: {', '.join(display_cmds)}")

        if self.learned_patterns:
            display_pats = self.learned_patterns[:5]
            lines.append("Learned patterns:")
            for pat in display_pats:
                lines.append(f"  - {pat}")

        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _has_useful_content(self) -> bool:
        return bool(
            self.project_type
            or self.key_files
            or (self.architecture_notes and not self._notes_expired())
            or self.common_commands
            or self.learned_patterns
        )

    def _notes_expired(self) -> bool:
        if not self.last_updated:
            return True
        age_days = (time.time() - self.last_updated) / 86400
        return age_days > _NOTES_EXPIRY_DAYS


def _ensure_gitignore(metis_dir: Path) -> None:
    """确保 .metis/.gitignore 包含 memory.json。"""
    gitignore = metis_dir / ".gitignore"
    try:
        if gitignore.exists():
            content = gitignore.read_text(encoding="utf-8")
            if _MEMORY_FILE in content:
                return
            content = content.rstrip("\n") + f"\n{_MEMORY_FILE}\n"
        else:
            content = f"# Metis local data — do not commit\n{_MEMORY_FILE}\n"
        gitignore.write_text(content, encoding="utf-8")
    except Exception:
        pass  # 非关键操作
