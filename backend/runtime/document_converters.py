from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ConverterCandidate:
    name: str
    path: str
    source: str
    available: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "source": self.source,
            "available": self.available,
        }


@dataclass(frozen=True)
class DocumentConverterStatus:
    soffice: ConverterCandidate | None
    antiword: ConverterCandidate | None
    pandoc: ConverterCandidate | None
    xlrd: ConverterCandidate | None
    search_roots: tuple[str, ...]

    @property
    def can_doc(self) -> bool:
        return _available(self.soffice) or _available(self.antiword)

    @property
    def can_xls(self) -> bool:
        return _available(self.xlrd) or _available(self.soffice)

    @property
    def can_ppt(self) -> bool:
        return _available(self.soffice)

    def support_for(self, ext: str) -> bool:
        if ext == ".doc":
            return self.can_doc
        if ext == ".xls":
            return self.can_xls
        if ext == ".ppt":
            return self.can_ppt
        return False

    def to_dict(self) -> dict[str, Any]:
        support = {
            "doc": self.can_doc,
            "xls": self.can_xls,
            "ppt": self.can_ppt,
        }
        converters = {
            "soffice": self.soffice.to_dict() if self.soffice else None,
            "antiword": self.antiword.to_dict() if self.antiword else None,
            "pandoc": self.pandoc.to_dict() if self.pandoc else None,
            "xlrd": self.xlrd.to_dict() if self.xlrd else None,
        }
        missing = [
            name
            for name, available in support.items()
            if not available
        ]
        return {
            "ok": any(support.values()),
            "schema": "metis.document_converter_status.v1",
            "support": support,
            "missing": missing,
            "converters": converters,
            "search_roots": list(self.search_roots),
            "recommended_roots": recommended_converter_roots(),
            "hints": converter_hints(self),
        }


def document_converter_status() -> DocumentConverterStatus:
    roots = tuple(_converter_search_roots())
    return DocumentConverterStatus(
        soffice=find_converter(("soffice", "libreoffice"), roots=roots, env_var="METIS_SOFFICE"),
        antiword=find_converter(("antiword",), roots=roots, env_var="METIS_ANTIWORD"),
        pandoc=find_converter(("pandoc",), roots=roots, env_var="METIS_PANDOC"),
        xlrd=_python_module_candidate("xlrd"),
        search_roots=roots,
    )


def find_converter(
    names: Iterable[str],
    *,
    roots: Iterable[str] | None = None,
    env_var: str = "",
) -> ConverterCandidate | None:
    if env_var:
        explicit = os.environ.get(env_var, "").strip().strip('"')
        candidate = _candidate_from_path(names, explicit, source=env_var)
        if candidate:
            return candidate

    for name in names:
        found = shutil.which(name)
        if found:
            return ConverterCandidate(name=name, path=found, source="PATH", available=True)

    for root in roots or ():
        for path in _candidate_paths(Path(root), names):
            if path.is_file():
                return ConverterCandidate(name=path.stem, path=str(path), source=str(root), available=True)
    return None


def soffice_path() -> str:
    status = document_converter_status()
    return status.soffice.path if status.soffice and status.soffice.available else ""


def antiword_path() -> str:
    status = document_converter_status()
    return status.antiword.path if status.antiword and status.antiword.available else ""


def converter_hints(status: DocumentConverterStatus | None = None) -> list[str]:
    current = status or document_converter_status()
    hints: list[str] = []
    if not current.can_doc:
        hints.append(".doc 需要 LibreOffice soffice 或 antiword；可设置 METIS_SOFFICE/METIS_ANTIWORD，或放入便携转换器目录。")
    if not current.can_xls:
        hints.append(".xls 优先使用纯 Python xlrd；打包版会内置 xlrd，开发环境可 pip install xlrd。")
    if not current.can_ppt:
        hints.append(".ppt 需要 LibreOffice soffice；旧二进制 PPT 没有可靠的纯 Python 通用解析方案。")
    if _available(current.pandoc):
        hints.append("已检测到 Pandoc；它适合文本/文档转换，但不能单独可靠解析旧版 .doc/.xls/.ppt。")
    return hints


def recommended_converter_roots() -> list[str]:
    roots = _converter_search_roots()
    preferred = [
        root
        for root in roots
        if "resources" in root.lower() or "document-converters" in root.lower()
    ]
    return list(dict.fromkeys(preferred[:4] or roots[:4]))


def _converter_search_roots() -> list[str]:
    roots: list[str] = []
    env_roots = os.environ.get("METIS_DOCUMENT_CONVERTER_PATHS", "")
    roots.extend(part.strip() for part in env_roots.split(os.pathsep) if part.strip())

    for base in _runtime_roots():
        roots.extend(
            str(base / rel)
            for rel in (
                Path("tools") / "document-converters",
                Path("resources") / "document-converters",
                Path("resources") / "LibreOffice" / "program",
                Path("LibreOffice") / "program",
            )
        )
    return tuple(dict.fromkeys(roots))


def _runtime_roots() -> list[Path]:
    roots = [
        Path.cwd(),
        Path(__file__).resolve(strict=False).parents[2],
    ]
    executable = Path(getattr(sys, "executable", "") or "")
    if executable:
        roots.append(executable.resolve(strict=False).parent)
    return list(dict.fromkeys(roots))


def _candidate_paths(root: Path, names: Iterable[str]) -> list[Path]:
    suffixes = [".exe", ".cmd", ".bat", ""] if os.name == "nt" else ["", ".sh"]
    paths: list[Path] = []
    for name in names:
        for suffix in suffixes:
            paths.append(root / f"{name}{suffix}")
        for subdir in ("program", "bin"):
            for suffix in suffixes:
                paths.append(root / subdir / f"{name}{suffix}")
    return paths


def _candidate_from_path(names: Iterable[str], raw: str, *, source: str) -> ConverterCandidate | None:
    if not raw:
        return None
    path = Path(raw).expanduser()
    candidates = _candidate_paths(path, names) if path.is_dir() else [path]
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved.is_file():
            return ConverterCandidate(name=resolved.stem, path=str(resolved), source=source, available=True)
    return None


def _python_module_candidate(module_name: str) -> ConverterCandidate | None:
    try:
        module = __import__(module_name)
    except Exception:
        return None
    path = str(getattr(module, "__file__", "") or "")
    return ConverterCandidate(name=module_name, path=path, source="python", available=True)


def _available(candidate: ConverterCandidate | None) -> bool:
    return bool(candidate and candidate.available and candidate.path)
