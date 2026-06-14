"""workspace_memory.py 单元测试 — 验证持久化、更新和 prompt 注入。"""
from __future__ import annotations

import json
import time

import pytest

from backend.core.memory.workspace_memory import WorkspaceMemory


# ---------------------------------------------------------------------------
# 创建与加载
# ---------------------------------------------------------------------------

class TestLoadSave:
    def test_load_nonexistent_returns_empty(self, tmp_path):
        mem = WorkspaceMemory.load(str(tmp_path))
        assert mem.workspace_root == str(tmp_path)
        assert mem.project_type == ""
        assert mem.key_files == []

    def test_save_creates_file(self, tmp_path):
        mem = WorkspaceMemory(workspace_root=str(tmp_path), project_type="python")
        mem.save()
        assert (tmp_path / ".metis" / "memory.json").exists()

    def test_save_and_load_roundtrip(self, tmp_path):
        mem = WorkspaceMemory(
            workspace_root=str(tmp_path),
            project_type="typescript",
            key_files=["src/main.ts", "src/app.tsx"],
            architecture_notes="React + Express",
            common_commands=["npm test", "npm run build"],
            learned_patterns=["Uses Redux for state"],
        )
        mem.save()
        loaded = WorkspaceMemory.load(str(tmp_path))
        assert loaded.project_type == "typescript"
        assert loaded.key_files == ["src/main.ts", "src/app.tsx"]
        assert loaded.architecture_notes == "React + Express"
        assert loaded.common_commands == ["npm test", "npm run build"]
        assert loaded.learned_patterns == ["Uses Redux for state"]

    def test_load_ignores_unknown_fields(self, tmp_path):
        path = tmp_path / ".metis" / "memory.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "project_type": "python",
            "unknown_field": "hello",
            "another": 42,
        }))
        mem = WorkspaceMemory.load(str(tmp_path))
        assert mem.project_type == "python"

    def test_load_corrupted_file_returns_empty(self, tmp_path):
        path = tmp_path / ".metis" / "memory.json"
        path.parent.mkdir(parents=True)
        path.write_text("not json {{{")
        mem = WorkspaceMemory.load(str(tmp_path))
        assert mem.project_type == ""

    def test_gitignore_created(self, tmp_path):
        mem = WorkspaceMemory(workspace_root=str(tmp_path), project_type="python")
        mem.save()
        gitignore = tmp_path / ".metis" / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text(encoding="utf-8")
        assert "memory.json" in content

    def test_gitignore_not_duplicated(self, tmp_path):
        mem = WorkspaceMemory(workspace_root=str(tmp_path), project_type="python")
        mem.save()
        mem.save()  # save again
        content = (tmp_path / ".metis" / ".gitignore").read_text(encoding="utf-8")
        assert content.count("memory.json") == 1


# ---------------------------------------------------------------------------
# 更新方法
# ---------------------------------------------------------------------------

class TestUpdates:
    def test_set_project_type(self):
        mem = WorkspaceMemory(workspace_root="/tmp")
        mem.set_project_type("python")
        assert mem.project_type == "python"

    def test_set_project_type_empty_noop(self):
        mem = WorkspaceMemory(workspace_root="/tmp", project_type="python")
        mem.set_project_type("")
        assert mem.project_type == "python"

    def test_add_key_file(self):
        mem = WorkspaceMemory(workspace_root="/tmp")
        mem.add_key_file("src/main.py")
        assert "src/main.py" in mem.key_files

    def test_add_key_file_no_duplicates(self):
        mem = WorkspaceMemory(workspace_root="/tmp")
        mem.add_key_file("a.py")
        mem.add_key_file("a.py")
        assert mem.key_files.count("a.py") == 1

    def test_add_key_file_cap(self):
        mem = WorkspaceMemory(workspace_root="/tmp")
        for i in range(20):
            mem.add_key_file(f"file_{i}.py")
        assert len(mem.key_files) <= 15

    def test_add_common_command(self):
        mem = WorkspaceMemory(workspace_root="/tmp")
        mem.add_common_command("pytest")
        assert "pytest" in mem.common_commands

    def test_add_common_command_dedup_moves_to_end(self):
        mem = WorkspaceMemory(workspace_root="/tmp")
        mem.add_common_command("pytest")
        mem.add_common_command("npm test")
        mem.add_common_command("pytest")  # move to end
        assert mem.common_commands == ["npm test", "pytest"]

    def test_add_common_command_cap(self):
        mem = WorkspaceMemory(workspace_root="/tmp")
        for i in range(15):
            mem.add_common_command(f"cmd_{i}")
        assert len(mem.common_commands) <= 10

    def test_add_learned_pattern(self):
        mem = WorkspaceMemory(workspace_root="/tmp")
        mem.add_learned_pattern("Uses Flask blueprints")
        assert "Uses Flask blueprints" in mem.learned_patterns

    def test_add_learned_pattern_no_dup(self):
        mem = WorkspaceMemory(workspace_root="/tmp")
        mem.add_learned_pattern("x")
        mem.add_learned_pattern("x")
        assert mem.learned_patterns.count("x") == 1

    def test_update_architecture_notes(self):
        mem = WorkspaceMemory(workspace_root="/tmp")
        mem.update_architecture_notes("Flask + React")
        assert mem.architecture_notes == "Flask + React"

    def test_update_architecture_notes_empty_noop(self):
        mem = WorkspaceMemory(workspace_root="/tmp", architecture_notes="old")
        mem.update_architecture_notes("")
        assert mem.architecture_notes == "old"


# ---------------------------------------------------------------------------
# Prompt 注入
# ---------------------------------------------------------------------------

class TestToPromptBlock:
    def test_empty_memory_returns_empty(self):
        mem = WorkspaceMemory(workspace_root="/tmp")
        assert mem.to_prompt_block() == ""

    def test_with_project_type(self):
        mem = WorkspaceMemory(workspace_root="/tmp", project_type="python")
        block = mem.to_prompt_block()
        assert "Project type: python" in block

    def test_with_key_files(self):
        mem = WorkspaceMemory(
            workspace_root="/tmp",
            key_files=["a.py", "b.py"],
        )
        block = mem.to_prompt_block()
        assert "a.py" in block
        assert "b.py" in block

    def test_with_architecture_notes(self):
        mem = WorkspaceMemory(
            workspace_root="/tmp",
            architecture_notes="Flask blueprints",
            last_updated=time.time(),  # fresh
        )
        block = mem.to_prompt_block()
        assert "Flask blueprints" in block

    def test_expired_notes_omitted(self):
        mem = WorkspaceMemory(
            workspace_root="/tmp",
            architecture_notes="Very old notes",
            last_updated=time.time() - 8 * 86400,  # 8 days ago
        )
        block = mem.to_prompt_block()
        # Notes expired, but if there's nothing else useful, block is empty
        assert "Very old notes" not in block

    def test_common_commands_included(self):
        mem = WorkspaceMemory(
            workspace_root="/tmp",
            common_commands=["pytest", "npm test"],
        )
        block = mem.to_prompt_block()
        assert "pytest" in block

    def test_learned_patterns_included(self):
        mem = WorkspaceMemory(
            workspace_root="/tmp",
            learned_patterns=["Uses Redux"],
        )
        block = mem.to_prompt_block()
        assert "Uses Redux" in block

    def test_long_notes_truncated(self):
        mem = WorkspaceMemory(
            workspace_root="/tmp",
            architecture_notes="x" * 1000,
            last_updated=time.time(),
        )
        block = mem.to_prompt_block()
        assert "..." in block
        assert len(block) < 800

    def test_block_starts_with_section_header(self):
        mem = WorkspaceMemory(
            workspace_root="/tmp",
            project_type="python",
        )
        block = mem.to_prompt_block()
        assert "Project Memory" in block
