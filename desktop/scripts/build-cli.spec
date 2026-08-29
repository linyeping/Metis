# -*- mode: python ; coding: utf-8 -*-
import ast
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

SPEC_ROOT = Path(SPECPATH).resolve()
DESKTOP_ROOT = SPEC_ROOT.parent
REPO_ROOT = DESKTOP_ROOT.parent
BACKEND_ROOT = REPO_ROOT / "backend"

EXCLUDED_DATA_PARTS = {
    "__pycache__",
    ".delegate_sessions",
    ".metis",
    ".pytest_cache",
    ".ruff_cache",
    ".vscode",
    "build",
    "metis_backend.egg-info",
    "others",
    "output",
    "packaging_blueprint",
    "tests",
    "var",
    "metis-vm-svc",
    "vmpack_build",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".exe", ".go", ".vhdx"}


def collect_source_tree(source: Path, target: str):
    entries = []
    if not source.exists():
        return entries
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if any(part in EXCLUDED_DATA_PARTS for part in relative.parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES or path.name.startswith(".env"):
            continue
        entries.append((str(path), str(Path(target) / relative.parent)))
    return entries


def collect_tool_registry_imports():
    registry_path = BACKEND_ROOT / "tools" / "registry.py"
    if not registry_path.exists():
        return []
    tree = ast.parse(registry_path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("backend.tools."):
            modules.add(node.module)
    return sorted(modules)


datas = []
for source_name in ("runtime", "tools", "core", "web", "bridges", "assets", "resources", "cli"):
    datas += collect_source_tree(BACKEND_ROOT / source_name, str(Path("backend") / source_name))
binaries = []
hiddenimports = []
hiddenimports += collect_submodules("backend")
hiddenimports += collect_submodules("backend.cli")
hiddenimports += collect_submodules("backend.runtime")
hiddenimports += collect_submodules("backend.web")
hiddenimports += collect_submodules("backend.core")
hiddenimports += collect_submodules("backend.bridges")
hiddenimports += collect_submodules("backend.tools")
hiddenimports += collect_tool_registry_imports()

for package_name in (
    "flask",
    "pynput",
    "pyautogui",
    "pyscreeze",
    "pygetwindow",
    "pyrect",
    "mouseinfo",
    "pymsgbox",
    "pytweening",
    "pyperclip",
    "docx",
    "pypdf",
    "pdfplumber",
    "reportlab",
    "prompt_toolkit",
):
    try:
        package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_hiddenimports
    except Exception:
        pass

excluded_modules = [
    "IPython",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "alabaster",
    "altair",
    "astropy",
    "bokeh",
    "browser_use",
    "cv2",
    "dask",
    "distributed",
    "ipykernel",
    "ipywidgets",
    "jupyter",
    "jupyter_client",
    "jupyter_core",
    "jupyter_server",
    "jupyterlab",
    "keras",
    "langchain_anthropic",
    "langchain_google_genai",
    "langchain_openai",
    "llvmlite",
    "matplotlib",
    "notebook",
    "numba",
    "opencv_python",
    "paddle",
    "paddleocr",
    "panel",
    "pandas",
    "playwright",
    "pyarrow",
    "pytest",
    "scipy",
    "seaborn",
    "sentence_transformers",
    "sklearn",
    "sphinx",
    "tensorflow",
    "tensorflow_estimator",
    "torch",
    "torchaudio",
    "torchvision",
    "ultralytics",
]

a = Analysis(
    [str(BACKEND_ROOT / "cli_entry.py")],
    pathex=[str(REPO_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="metis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
