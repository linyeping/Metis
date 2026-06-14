"""agent_state 工具落盘文件名（与 memory/workspace_state 共用真源）。"""

AGENT_TODO_FILE = ".agent_todos.json"
AGENT_MODE_FILE = ".agent_mode.json"

# 与 engine/open_files_context、write_open_files_context 共用
METIS_OPEN_FILES_JSON = ".metis_open_files.json"
METIS_OPEN_FILES_TXT = ".metis_open_files.txt"

# Backward-compatible import names used by older modules.
MIRO_OPEN_FILES_JSON = METIS_OPEN_FILES_JSON
MIRO_OPEN_FILES_TXT = METIS_OPEN_FILES_TXT
LEGACY_MIRO_OPEN_FILES_JSON = ".miro_open_files.json"
LEGACY_MIRO_OPEN_FILES_TXT = ".miro_open_files.txt"
