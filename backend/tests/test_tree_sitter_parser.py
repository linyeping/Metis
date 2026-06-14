"""tree_sitter_parser 单元测试 —— 验证 AST 签名提取的正确性。"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from backend.tools.coding.foundation.tree_sitter_parser import (
    SignatureNode,
    detect_language,
    parse_file_signatures,
)


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    def test_python(self):
        assert detect_language("foo.py") == "python"

    def test_javascript(self):
        assert detect_language("index.js") == "javascript"

    def test_jsx(self):
        assert detect_language("App.jsx") == "javascript"

    def test_typescript(self):
        assert detect_language("utils.ts") == "typescript"

    def test_tsx(self):
        assert detect_language("Component.tsx") == "tsx"

    def test_unsupported_extension_returns_none(self):
        assert detect_language("main.go") is None
        assert detect_language("lib.rs") is None
        assert detect_language("data.json") is None

    def test_case_insensitive_extension(self):
        assert detect_language("FOO.PY") == "python"
        assert detect_language("Bar.TS") == "typescript"

    def test_no_extension(self):
        assert detect_language("Makefile") is None


# ---------------------------------------------------------------------------
# Python 签名提取
# ---------------------------------------------------------------------------


class TestParsePythonSignatures:
    def _write_and_parse(self, tmp_path: Path, code: str):
        p = tmp_path / "sample.py"
        p.write_text(textwrap.dedent(code), encoding="utf-8")
        return parse_file_signatures(str(p), "python")

    def test_top_level_function(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            def greet(name: str) -> str:
                return f"hello {name}"
        """)
        assert len(sigs) == 1
        assert sigs[0].kind == "function"
        assert sigs[0].name == "greet"
        assert "name: str" in sigs[0].signature

    def test_class_with_methods(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            class Calculator:
                def add(self, a, b):
                    return a + b
                def sub(self, a, b):
                    return a - b
        """)
        assert len(sigs) == 1
        cls = sigs[0]
        assert cls.kind == "class"
        assert cls.name == "Calculator"
        assert len(cls.children) == 2
        assert cls.children[0].kind == "method"
        assert cls.children[0].name == "add"
        assert cls.children[1].name == "sub"

    def test_class_with_bases(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            class Dog(Animal):
                def bark(self):
                    pass
        """)
        assert len(sigs) == 1
        assert "Animal" in sigs[0].signature

    def test_decorated_function(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            @app.route("/")
            def index():
                return "ok"
        """)
        assert len(sigs) == 1
        assert sigs[0].kind == "function"
        assert sigs[0].name == "index"

    def test_decorated_class(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            @dataclass
            class Point:
                x: int
                y: int
        """)
        assert len(sigs) == 1
        assert sigs[0].kind == "class"
        assert sigs[0].name == "Point"

    def test_multiple_definitions(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            def foo():
                pass

            class Bar:
                pass

            def baz():
                pass
        """)
        assert len(sigs) == 3
        assert [s.name for s in sigs] == ["foo", "Bar", "baz"]

    def test_line_numbers(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            def first():
                pass

            def second():
                pass
        """)
        assert sigs[0].line == 1
        assert sigs[1].line == 4

    def test_empty_file(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, "")
        assert sigs == []

    def test_return_type_annotation(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            def fetch(url: str) -> dict:
                pass
        """)
        assert len(sigs) == 1
        assert "-> dict" in sigs[0].signature


# ---------------------------------------------------------------------------
# JavaScript / TypeScript 签名提取
# ---------------------------------------------------------------------------


class TestParseJsTsSignatures:
    def _write_and_parse(self, tmp_path: Path, code: str, ext: str = ".js"):
        lang_map = {".js": "javascript", ".ts": "typescript", ".tsx": "tsx"}
        p = tmp_path / f"sample{ext}"
        p.write_text(textwrap.dedent(code), encoding="utf-8")
        return parse_file_signatures(str(p), lang_map[ext])

    def test_function_declaration(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            function greet(name) {
                return `hello ${name}`;
            }
        """)
        assert len(sigs) == 1
        assert sigs[0].kind == "function"
        assert sigs[0].name == "greet"

    def test_class_declaration(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            class Animal {
                constructor() {}
                speak() {}
            }
        """)
        assert len(sigs) == 1
        cls = sigs[0]
        assert cls.kind == "class"
        assert cls.name == "Animal"
        assert len(cls.children) >= 1

    def test_arrow_function_const(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            const add = (a, b) => a + b;
        """)
        assert len(sigs) == 1
        assert sigs[0].kind == "function"
        assert sigs[0].name == "add"

    def test_exported_function(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            export function fetchData() {
                return [];
            }
        """)
        assert len(sigs) == 1
        assert sigs[0].name == "fetchData"

    def test_typescript_interface(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            interface UserProps {
                name: string;
                age: number;
            }
        """, ext=".ts")
        assert len(sigs) == 1
        assert sigs[0].kind == "interface"
        assert sigs[0].name == "UserProps"

    def test_typescript_type_alias(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            type Status = "active" | "inactive";
        """, ext=".ts")
        assert len(sigs) == 1
        assert sigs[0].kind == "type"
        assert sigs[0].name == "Status"

    def test_tsx_component(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            export function App() {
                return <div>Hello</div>;
            }
        """, ext=".tsx")
        assert len(sigs) == 1
        assert sigs[0].name == "App"

    def test_export_default_class(self, tmp_path):
        sigs = self._write_and_parse(tmp_path, """\
            export default class Store {
                get() {}
            }
        """)
        assert len(sigs) == 1
        assert sigs[0].name == "Store"


# ---------------------------------------------------------------------------
# 边界情况
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_nonexistent_file(self):
        sigs = parse_file_signatures("/no/such/file.py", "python")
        assert sigs == []

    def test_unsupported_language_returns_empty(self, tmp_path):
        p = tmp_path / "main.go"
        p.write_text("package main\nfunc main() {}\n")
        sigs = parse_file_signatures(str(p), "go")
        assert sigs == []

    def test_auto_detect_from_path(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("def auto():\n    pass\n")
        sigs = parse_file_signatures(str(p))  # language=None, auto-detect
        assert len(sigs) == 1
        assert sigs[0].name == "auto"

    def test_large_file_skipped(self, tmp_path):
        p = tmp_path / "huge.py"
        p.write_bytes(b"x = 1\n" * 200_000)  # > 512KB
        sigs = parse_file_signatures(str(p), "python")
        assert sigs == []

    def test_signature_node_dataclass(self):
        node = SignatureNode(kind="function", name="f", signature="def f()")
        assert node.children == []
        assert node.line == 0
