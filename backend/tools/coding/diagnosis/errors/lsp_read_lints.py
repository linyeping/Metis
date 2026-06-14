# -*- coding: utf-8 -*-
"""
LSP 诊断客户端（read_lints 的 LSP 模式）。

使用 LSP 规范的 **Content-Length** stdio 分帧（非 NDJSON）。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from shutil import which
from typing import Any, Dict, List, Optional, Tuple

# 语言 ID（与 LSP languageId 一致，并用于 DEFAULT_LSP_COMMANDS 键）
LANGUAGE_ID_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascriptreact",
    ".tsx": "typescriptreact",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".fs": "fsharp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".clj": "clojure",
    ".lua": "lua",
    ".r": "r",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".md": "markdown",
    ".txt": "plaintext",
}

def _command0_executable(cmd0: str) -> bool:
    """Windows 下首参常为绝对路径 python.exe，shutil.which 不可靠。"""
    if not cmd0:
        return False
    expanded = os.path.expandvars(os.path.expanduser(cmd0))
    if os.path.isfile(expanded):
        return True
    return bool(which(cmd0))


DEFAULT_LSP_COMMANDS = {
    "python": ["pylsp"],
    "javascript": ["typescript-language-server", "--stdio"],
    "typescript": ["typescript-language-server", "--stdio"],
    "java": ["java-language-server"],
    "go": ["gopls"],
    "rust": ["rust-analyzer"],
    "cpp": ["clangd"],
    "c": ["clangd"],
}


def _parse_one_message(buf: bytes) -> Optional[Tuple[Dict[str, Any], bytes]]:
    """从缓冲区解析一条 LSP 消息；不足则返回 None。"""
    sep = b"\r\n\r\n"
    idx = buf.find(sep)
    if idx < 0:
        return None
    header_blob = buf[:idx].decode("ascii", errors="replace")
    body_start = idx + len(sep)
    content_length: Optional[int] = None
    for line in header_blob.split("\r\n"):
        if line.lower().startswith("content-length:"):
            try:
                content_length = int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
            break
    if content_length is None:
        return None
    if len(buf) < body_start + content_length:
        return None
    raw = buf[body_start : body_start + content_length]
    rest = buf[body_start + content_length :]
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj, rest


def _normalize_file_key(path: str) -> str:
    """用于与 publishDiagnostics 回写路径对齐。"""
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))
    except OSError:
        return os.path.normpath(path)


class LSPClient:
    """LSP JSON-RPC over stdio（Content-Length）。"""

    def __init__(self, server_cmd: List[str], workspace_root: str, timeout_sec: int = 30):
        self.server_cmd = server_cmd
        self.workspace_root = os.path.abspath(workspace_root)
        self.timeout_sec = timeout_sec
        self.process: Optional[subprocess.Popen] = None
        self._msg_id = 0
        self._lock = threading.Lock()
        self._rpc_replies: Dict[int, Dict[str, Any]] = {}
        self._rpc_event = threading.Event()
        self.diagnostics: Dict[str, List[Dict[str, Any]]] = {}
        self.initialized = False
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._running = False

    def _next_id(self) -> int:
        with self._lock:
            self._msg_id += 1
            return self._msg_id

    def _write_message(self, obj: Dict[str, Any]) -> bool:
        if not self.process or not self.process.stdin:
            return False
        try:
            body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
            header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            self.process.stdin.write(header + body)
            self.process.stdin.flush()
            return True
        except (BrokenPipeError, OSError, TypeError, ValueError):
            return False

    def _wait_rpc_result(self, req_id: int, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        deadline = time.time() + (timeout if timeout is not None else self.timeout_sec)
        while time.time() < deadline:
            with self._lock:
                if req_id in self._rpc_replies:
                    return self._rpc_replies.pop(req_id)
            self._rpc_event.wait(timeout=0.05)
            self._rpc_event.clear()
        return None

    def _handle_incoming(self, msg: Dict[str, Any]) -> None:
        if msg.get("method") == "textDocument/publishDiagnostics":
            params = msg.get("params") or {}
            uri = params.get("uri", "")
            diagnostics = params.get("diagnostics") or []
            path = self._uri_to_path(uri)
            if path:
                try:
                    key = _normalize_file_key(str(Path(path).resolve(strict=False)))
                except OSError:
                    key = _normalize_file_key(path)
                with self._lock:
                    self.diagnostics[key] = diagnostics
            return
        if "id" in msg and "result" in msg:
            with self._lock:
                self._rpc_replies[int(msg["id"])] = msg
            self._rpc_event.set()

    def _reader_loop(self) -> None:
        buf = b""
        assert self.process and self.process.stdout
        while self._running and self.process.poll() is None:
            try:
                chunk = self.process.stdout.read(4096)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buf += chunk
            while True:
                parsed = _parse_one_message(buf)
                if parsed is None:
                    break
                msg, buf = parsed
                self._handle_incoming(msg)

    def _drain_stderr(self) -> None:
        assert self.process and self.process.stderr
        try:
            while self._running and self.process.poll() is None:
                line = self.process.stderr.readline()
                if not line:
                    break
        except (OSError, ValueError):
            pass

    def start(self) -> bool:
        exe = self.server_cmd[0]
        if not _command0_executable(exe):
            return False
        try:
            child_env = os.environ.copy()
            child_env.setdefault("PYTHONUNBUFFERED", "1")
            self.process = subprocess.Popen(
                self.server_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.workspace_root,
                bufsize=0,
                env=child_env,
            )
        except OSError:
            return False

        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

        init_id = self._next_id()
        ok = self._write_message(
            {
                "jsonrpc": "2.0",
                "id": init_id,
                "method": "initialize",
                "params": {
                    "processId": os.getpid(),
                    "rootUri": Path(self.workspace_root).resolve().as_uri(),
                    "capabilities": {
                        "textDocument": {
                            "publishDiagnostics": {"relatedInformation": True},
                            "synchronization": {"didSave": True},
                        },
                        "workspace": {"configuration": True},
                    },
                    "trace": "off",
                },
            }
        )
        if not ok:
            return False
        rep = self._wait_rpc_result(init_id)
        if not rep or "result" not in rep:
            return False
        self.initialized = True
        self._write_message({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        return True

    def get_diagnostics(self, file_path: str) -> List[Dict[str, Any]]:
        if not self.initialized:
            return []
        try:
            key = _normalize_file_key(str(Path(file_path).resolve(strict=False)))
        except OSError:
            key = _normalize_file_key(file_path)
        with self._lock:
            self.diagnostics.pop(key, None)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            return []

        ext = os.path.splitext(file_path)[1].lower()
        language_id = LANGUAGE_ID_MAP.get(ext, "plaintext")
        uri = Path(os.path.abspath(file_path)).resolve().as_uri()

        did_open = {
            "jsonrpc": "2.0",
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": 1,
                    "text": content,
                }
            },
        }
        self._write_message(did_open)

        deadline = time.time() + self.timeout_sec
        try:
            want_resolved = Path(file_path).resolve(strict=False)
        except OSError:
            want_resolved = None
        while time.time() < deadline:
            with self._lock:
                if key in self.diagnostics:
                    return list(self.diagnostics.get(key, []))
                if want_resolved is not None:
                    for k, v in self.diagnostics.items():
                        try:
                            if os.path.normcase(Path(k).resolve(strict=False)) == os.path.normcase(
                                want_resolved
                            ):
                                return list(v)
                        except OSError:
                            continue
            time.sleep(0.05)
        return []

    def stop(self) -> None:
        self._running = False
        if self.process:
            try:
                if self.initialized:
                    sid = self._next_id()
                    if self._write_message({"jsonrpc": "2.0", "id": sid, "method": "shutdown", "params": None}):
                        self._wait_rpc_result(sid, timeout=min(5.0, float(self.timeout_sec)))
                    self._write_message({"jsonrpc": "2.0", "method": "exit", "params": {}})
                self.process.stdin.close()
            except OSError:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            self.process = None

    def _uri_to_path(self, uri: str) -> Optional[str]:
        try:
            parsed = urllib.parse.urlparse(uri)
            if parsed.scheme != "file":
                return None
            raw_path = urllib.parse.unquote(parsed.path or "")
            if os.name == "nt" and raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
                raw_path = raw_path[1:]
            local = urllib.request.url2pathname(raw_path)
            return os.path.normpath(local)
        except Exception:
            return None


def get_language_id(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    return LANGUAGE_ID_MAP.get(ext, "plaintext")


def get_lsp_command(language: str, custom_command: Optional[str] = None) -> Optional[List[str]]:
    if custom_command:
        import shlex

        try:
            return shlex.split(custom_command, posix=os.name != "nt")
        except ValueError:
            return None
    return DEFAULT_LSP_COMMANDS.get(language)


def format_diagnostics(diagnostics: List[Dict[str, Any]], file_path: str, max_output: int) -> str:
    if not diagnostics:
        return ""
    lines: List[str] = []
    for diag in diagnostics:
        range_info = diag.get("range") or {}
        start = range_info.get("start") or {}
        line = int(start.get("line", 0)) + 1
        character = int(start.get("character", 0)) + 1
        severity = diag.get("severity", 1)
        severity_map = {1: "error", 2: "warning", 3: "info", 4: "hint"}
        severity_str = severity_map.get(severity, "info")
        code = diag.get("code")
        message = diag.get("message", "")
        code_part = f" [{code}]" if code else ""
        lines.append(f"{file_path}:{line}:{character} [{severity_str}]{code_part} {message}")
    output = "\n".join(lines)
    if len(output) > max_output:
        output = output[:max_output] + "\n... (截断)"
    return output


def try_lsp_diagnostics(
    file_paths: List[str],
    lsp_command: List[str],
    workspace_root: str,
    timeout_sec: int,
    max_output: int,
) -> Optional[str]:
    if not file_paths:
        return None
    client = LSPClient(lsp_command, workspace_root, timeout_sec)
    if not client.start():
        return None
    try:
        parts: List[str] = []
        for fp in file_paths:
            if not os.path.isfile(fp):
                continue
            diags = client.get_diagnostics(fp)
            if diags:
                formatted = format_diagnostics(diags, fp, max_output)
                if formatted:
                    parts.append(formatted)
        if parts:
            label = os.path.basename(lsp_command[-1]) if len(lsp_command) > 1 else os.path.basename(lsp_command[0])
            header = f"=== LSP ({label}) ===\n"
            return header + "\n".join(parts)
        return "=== LSP ===\n(no diagnostics; server returned empty)\n"
    finally:
        client.stop()
