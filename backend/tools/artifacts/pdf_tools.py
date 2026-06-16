from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from backend.tools.coding.foundation.core_mechanisms.path_security import (
    PathSecurityError,
    safe_path_for_read,
    safe_path_for_write,
)


def pdf_info(path: str) -> str:
    pdf, error = _resolve_existing_file(path)
    if pdf is None:
        return _json_error(error or "PDF file not found", path=path)
    info: Dict[str, Any] = {"ok": True, "path": str(pdf), "pages": 0, "metadata": {}}
    reader = _pypdf_reader(pdf)
    if reader is not None:
        info["pages"] = len(reader.pages)
        info["metadata"] = {str(k): str(v) for k, v in dict(reader.metadata or {}).items()}
        return _json(info)
    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo:
        proc = subprocess.run(
            [pdfinfo, str(pdf)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        info["pdfinfo"] = (proc.stdout or proc.stderr or "").strip()[:4000]
        info["ok"] = proc.returncode == 0
        return _json(info)
    return _json_error(
        "Missing PDF dependency. Install pypdf or Poppler pdfinfo.",
        path=str(pdf),
        dependency="pypdf or poppler",
    )


def pdf_extract_text(path: str, max_pages: int = 20) -> str:
    pdf, error = _resolve_existing_file(path)
    if pdf is None:
        return _json_error(error or "PDF file not found", path=path)
    limit = max(1, int(max_pages or 20))
    pages: List[Dict[str, Any]] = []
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(str(pdf)) as handle:
            for index, page in enumerate(handle.pages[:limit], start=1):
                pages.append({"page": index, "text": (page.extract_text() or "").strip()})
        return _json({"ok": True, "path": str(pdf), "method": "pdfplumber", "pages": pages})
    except Exception as exc:
        plumber_error = f"{type(exc).__name__}: {exc}"

    reader = _pypdf_reader(pdf)
    if reader is None:
        return _json_error(
            "Missing PDF text extraction dependency. Install pdfplumber or pypdf.",
            path=str(pdf),
            dependency="pdfplumber or pypdf",
            detail=plumber_error,
        )
    for index, page in enumerate(reader.pages[:limit], start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            text = f"[extract failed: {type(exc).__name__}: {exc}]"
        pages.append({"page": index, "text": text.strip()})
    return _json({"ok": True, "path": str(pdf), "method": "pypdf", "pages": pages})


def pdf_render_pages(
    path: str,
    output_dir: str = "",
    start_page: int = 1,
    end_page: int = 0,
    dpi: int = 150,
) -> str:
    pdf, error = _resolve_existing_file(path)
    if pdf is None:
        return _json_error(error or "PDF file not found", path=path)
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        return _json_error(
            "Missing Poppler renderer: pdftoppm was not found on PATH.",
            path=str(pdf),
            dependency="poppler pdftoppm",
            install_hint="Install poppler-utils or bundle Poppler with Metis.",
        )
    out_dir = _output_dir(output_dir, "output/pdf")
    prefix = out_dir / pdf.stem
    cmd = [pdftoppm, "-png", "-r", str(max(36, int(dpi or 150)))]
    first = max(1, int(start_page or 1))
    last = int(end_page or 0)
    if first > 1:
        cmd.extend(["-f", str(first)])
    if last >= first:
        cmd.extend(["-l", str(last)])
    cmd.extend([str(pdf), str(prefix)])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    images = sorted(str(path) for path in out_dir.glob(f"{pdf.stem}-*.png"))
    return _json(
        {
            "ok": proc.returncode == 0 and bool(images),
            "path": str(pdf),
            "output_dir": str(out_dir),
            "images": images,
            "command": cmd,
            "stderr": (proc.stderr or "")[:2000],
        }
    )


def pdf_screenshot_page(path: str, page: int = 1, output_path: str = "", dpi: int = 150) -> str:
    pdf, error = _resolve_existing_file(path)
    if pdf is None:
        return _json_error(error or "PDF file not found", path=path)
    out_dir = _output_dir(str(Path(output_path).parent) if output_path else "", "output/pdf")
    rendered = json.loads(pdf_render_pages(str(pdf), output_dir=str(out_dir), start_page=page, end_page=page, dpi=dpi))
    if not rendered.get("ok"):
        return _json(rendered)
    images = list(rendered.get("images") or [])
    if not images:
        return _json_error("No rendered page image was produced", path=str(pdf))
    image = Path(str(images[0]))
    if output_path:
        target = _resolve_output_path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(image, target)
        image = target
    return _json({"ok": True, "path": str(pdf), "page": int(page or 1), "image": str(image)})


def pdf_merge_split(
    input_paths: List[str] | str,
    output_path: str,
    pages: str = "",
) -> str:
    try:
        from pypdf import PdfReader, PdfWriter  # type: ignore
    except Exception as exc:
        return _json_error(
            "Missing PDF dependency: pypdf is required for merge/split.",
            dependency="pypdf",
            detail=f"{type(exc).__name__}: {exc}",
        )
    paths = [input_paths] if isinstance(input_paths, str) else list(input_paths or [])
    if not paths:
        return _json_error("No input PDF paths provided")
    writer = PdfWriter()
    added = 0
    for raw in paths:
        pdf, error = _resolve_existing_file(raw)
        if pdf is None:
            return _json_error(error or "PDF file not found", path=raw)
        reader = PdfReader(str(pdf))
        selected = _parse_page_selection(pages, len(reader.pages)) if pages else range(len(reader.pages))
        for index in selected:
            writer.add_page(reader.pages[index])
            added += 1
    target = _resolve_output_path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        writer.write(handle)
    return _json({"ok": True, "output_path": str(target), "pages_written": added})


def pdf_create(
    output_path: str,
    title: str = "",
    body: str = "",
    lines: List[str] | None = None,
) -> str:
    try:
        from reportlab.lib.pagesizes import LETTER  # type: ignore
        from reportlab.pdfgen import canvas  # type: ignore
    except Exception as exc:
        return _json_error(
            "Missing PDF creation dependency: reportlab is required.",
            dependency="reportlab",
            detail=f"{type(exc).__name__}: {exc}",
        )
    target = _resolve_output_path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(target), pagesize=LETTER)
    width, height = LETTER
    y = height - 72
    if title:
        c.setFont("Helvetica-Bold", 18)
        c.drawString(72, y, str(title)[:100])
        y -= 32
    c.setFont("Helvetica", 11)
    source_lines = list(lines or [])
    if body:
        source_lines.extend(str(body).splitlines())
    for line in source_lines or [""]:
        for wrapped in _wrap_line(str(line), max_chars=92):
            if y < 72:
                c.showPage()
                c.setFont("Helvetica", 11)
                y = height - 72
            c.drawString(72, y, wrapped)
            y -= 16
    c.save()
    return _json({"ok": True, "output_path": str(target), "title": title})


def _pypdf_reader(path: Path) -> Any:
    try:
        from pypdf import PdfReader  # type: ignore

        return PdfReader(str(path))
    except Exception:
        return None


def _resolve_existing_file(path: str) -> tuple[Path | None, str]:
    try:
        resolved = safe_path_for_read(str(path or ""))
    except PathSecurityError as exc:
        return None, str(exc)
    return (resolved, "") if resolved.is_file() else (None, "PDF file not found")


def _resolve_output_path(path: str) -> Path:
    return safe_path_for_write(str(path or ""))


def _output_dir(raw: str, default: str) -> Path:
    path = safe_path_for_write(str(raw or default))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse_page_selection(selection: str, page_count: int) -> List[int]:
    out: List[int] = []
    for part in str(selection or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            out.extend(range(max(1, int(start)), min(page_count, int(end)) + 1))
        else:
            out.append(int(part))
    return [index - 1 for index in out if 1 <= index <= page_count]


def _wrap_line(text: str, max_chars: int) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)]


def _json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _json_error(message: str, **extra: Any) -> str:
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return _json(payload)
