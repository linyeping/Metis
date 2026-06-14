# -*- coding: utf-8 -*-
"""
Miro 统一配置管理

配置优先级（从高到低）：
1. 环境变量（MIRO_* 前缀）
2. miro_config.json（工作区根目录）
3. 默认值

支持的配置项：
- workspace_root: 工作区根目录
- post_edit_lint: 写后自动 lint
- prune_threshold: 触发剪枝的字符数阈值
- shell_timeout: Shell 命令默认超时（秒）
- semantic_index_path: 语义搜索索引路径
- log_level: 日志级别
- full_unrestricted: 总闸，为 True 时所有「允许工作区外」分项视为开启
- allow_paths_outside_workspace / allow_shell_cwd_outside_workspace /
  allow_search_outside_workspace / allow_semantic_outside_workspace /
  allow_notebook_paths_outside_workspace /
  allow_delegate_subagent_outside_workspace: 分项（总闸关闭时生效）

环境变量示例：MIRO_FULL_UNRESTRICTED、MIRO_ALLOW_PATHS_OUTSIDE_WORKSPACE 等。
请求级覆盖见 execution_boundary_context.py。
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class MiroConfig:
    """Miro 配置管理器（单例）"""
    
    _instance: Optional['MiroConfig'] = None
    _config: Dict[str, Any] = {}
    _loaded: bool = False
    
    # 默认配置
    DEFAULTS = {
        "workspace_root": ".",
        "post_edit_lint": False,
        "prune_threshold": 10000,
        "shell_timeout": 60,
        "semantic_index_path": ".miro_index",
        "log_level": "INFO",
        "max_file_size_mb": 10,
        "allow_symlinks": False,
        "shell_blacklist": [],
        "openai_api_key": None,
        # 执行边界（阶段 B：真源；有效值可经 execution_boundary_context 合并总闸与 ContextVar）
        "full_unrestricted": False,
        "allow_paths_outside_workspace": False,
        "allow_shell_cwd_outside_workspace": False,
        "allow_search_outside_workspace": False,
        "allow_semantic_outside_workspace": False,
        "allow_notebook_paths_outside_workspace": False,
        "allow_delegate_subagent_outside_workspace": False,
    }
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._loaded:
            self._load_config()
            self._loaded = True
    
    def _load_config(self):
        """加载配置（环境变量 > JSON 文件 > 默认值）"""
        # 1. 从默认值开始
        self._config = dict(self.DEFAULTS)
        
        # 2. 从 JSON 文件加载
        config_path = Path("miro_config.json")
        if config_path.is_file():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                self._config.update(data)
            except Exception as e:
                # 配置文件解析失败，使用默认值
                pass
        
        # 3. 从环境变量覆盖（MIRO_* 前缀）
        for key in self.DEFAULTS.keys():
            env_key = f"MIRO_{key.upper()}"
            env_value = os.environ.get(env_key)
            if env_value is not None:
                # 类型转换
                default_type = type(self.DEFAULTS[key])
                if default_type == bool:
                    self._config[key] = env_value.lower() in ("1", "true", "yes", "on")
                elif default_type == int:
                    try:
                        self._config[key] = int(env_value)
                    except ValueError:
                        pass
                elif default_type == float:
                    try:
                        self._config[key] = float(env_value)
                    except ValueError:
                        pass
                else:
                    self._config[key] = env_value
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项"""
        return self._config.get(key, default)
    
    def set(self, key: str, value: Any):
        """设置配置项（运行时）"""
        self._config[key] = value
    
    def reload(self):
        """重新加载配置"""
        self._loaded = False
        self._load_config()
        self._loaded = True
    
    def to_dict(self) -> Dict[str, Any]:
        """导出配置为字典"""
        return dict(self._config)
    
    def save(self, path: Optional[str] = None):
        """保存配置到 JSON 文件"""
        if path is None:
            path = "miro_config.json"
        
        # 只保存非默认值
        to_save = {}
        for key, value in self._config.items():
            if key in self.DEFAULTS and value != self.DEFAULTS[key]:
                to_save[key] = value
        
        Path(path).write_text(
            json.dumps(to_save, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
    
    # 便捷属性访问
    @property
    def workspace_root(self) -> str:
        return self.get("workspace_root", ".")
    
    @property
    def post_edit_lint(self) -> bool:
        return self.get("post_edit_lint", False)
    
    @property
    def prune_threshold(self) -> int:
        return self.get("prune_threshold", 10000)
    
    @property
    def shell_timeout(self) -> int:
        return self.get("shell_timeout", 60)
    
    @property
    def semantic_index_path(self) -> str:
        return self.get("semantic_index_path", ".miro_index")
    
    @property
    def log_level(self) -> str:
        return self.get("log_level", "INFO")
    
    @property
    def max_file_size_mb(self) -> int:
        return self.get("max_file_size_mb", 10)
    
    @property
    def allow_symlinks(self) -> bool:
        return self.get("allow_symlinks", False)
    
    @property
    def shell_blacklist(self) -> list:
        return self.get("shell_blacklist", [])
    
    @property
    def openai_api_key(self) -> Optional[str]:
        """仅 ``web/config.py``，忽略本 JSON 中的 openai_api_key（避免多处存密钥）。"""
        try:
            import sys
            from pathlib import Path

            root = str(Path(__file__).resolve().parents[4])
            if root not in sys.path:
                sys.path.insert(0, root)
            from backend.web.config import resolve_openai_api_key

            w = resolve_openai_api_key().strip()
            return w or None
        except Exception:
            return None

    @property
    def full_unrestricted(self) -> bool:
        return self.get("full_unrestricted", False)

    @property
    def allow_paths_outside_workspace(self) -> bool:
        return self.get("allow_paths_outside_workspace", False)

    @property
    def allow_shell_cwd_outside_workspace(self) -> bool:
        return self.get("allow_shell_cwd_outside_workspace", False)

    @property
    def allow_search_outside_workspace(self) -> bool:
        return self.get("allow_search_outside_workspace", False)

    @property
    def allow_semantic_outside_workspace(self) -> bool:
        return self.get("allow_semantic_outside_workspace", False)

    @property
    def allow_notebook_paths_outside_workspace(self) -> bool:
        return self.get("allow_notebook_paths_outside_workspace", False)

    @property
    def allow_delegate_subagent_outside_workspace(self) -> bool:
        return self.get("allow_delegate_subagent_outside_workspace", False)


# 全局配置实例
config = MiroConfig()


def get_config() -> MiroConfig:
    """获取全局配置实例"""
    return config


__all__ = [
    "MiroConfig",
    "config",
    "get_config",
]
