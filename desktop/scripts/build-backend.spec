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
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


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
for source_name in ("runtime", "tools", "core", "web", "bridges", "assets", "resources"):
    datas += collect_source_tree(BACKEND_ROOT / source_name, str(Path("backend") / source_name))
binaries = []
hiddenimports = []
hiddenimports += collect_submodules("backend")
hiddenimports += collect_submodules("backend.runtime")
hiddenimports += collect_submodules("backend.web")
hiddenimports += collect_submodules("backend.core")
hiddenimports += collect_submodules("backend.bridges")
hiddenimports += collect_submodules("backend.tools")
hiddenimports += collect_tool_registry_imports()

flask_datas, flask_binaries, flask_hiddenimports = collect_all("flask")
datas += flask_datas
binaries += flask_binaries
hiddenimports += flask_hiddenimports

# 桌面自动化/急停依赖：computer use 的输入(pyautogui)与全局 ESC 监听(pynput)必须打进包，
# 否则 frozen 应用无法在运行时 `pip install`（sys.executable -m pip 不可用），导致接管时灵时不灵、
# ESC 急停失效。显式 collect pyautogui/pynput 及其运行时依赖。
for _auto_pkg in (
    "pynput",
    "pyautogui",
    "pyscreeze",
    "pygetwindow",
    "pyrect",
    "mouseinfo",
    "pymsgbox",
    "pytweening",
    "pyperclip",
):
    try:
        _ad, _ab, _ah = collect_all(_auto_pkg)
        datas += _ad
        binaries += _ab
        hiddenimports += _ah
    except Exception:
        pass  # 可选依赖缺失不阻断打包；运行时按需降级

# 文档/PDF artifact 工具依赖：PDF/DOCX 在 packaged exe 中必须可直接创建、读取与渲染。
for _artifact_pkg in (
    "docx",
    "pypdf",
    "pdfplumber",
    "reportlab",
):
    try:
        _ad, _ab, _ah = collect_all(_artifact_pkg)
        datas += _ad
        binaries += _ab
        hiddenimports += _ah
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
    [str(BACKEND_ROOT / "backend_entry.py")],
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
    [],
    exclude_binaries=True,
    name="metis-backend",
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
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="metis-backend",
)
