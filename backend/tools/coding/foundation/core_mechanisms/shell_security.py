# -*- coding: utf-8 -*-
"""
Shell 安全检查模块

防止危险命令执行，支持可配置的安全策略。

安全级别：
- disabled: 禁用检查
- warning: 警告但允许执行
- confirm: 需要确认（默认）
- deny: 直接拒绝

配置示例（miro_config.json）：
{
  "shell_security": {
    "enabled": true,
    "level": "confirm",
    "blacklist": ["rm -rf /", "dd if=/dev/zero"],
    "whitelist": ["git.*", "npm.*"],
    "allow_sudo": false
  }
}
"""
import re
from typing import List, Tuple

from backend.tools.coding.foundation.core_mechanisms.log_config import logger


class ShellSecurityError(Exception):
    """Shell 安全违规异常"""
    pass


# 默认危险命令模式（正则表达式）
DEFAULT_DANGEROUS_PATTERNS = [
    # 破坏性命令（高危）
    (r"rm\s+-rf\s+/\s*$", "删除根目录", "critical"),
    (r"rm\s+-rf\s+/\*", "删除根目录所有文件", "critical"),
    (r"rm\s+-rf\s+~", "删除用户主目录", "critical"),
    (r"dd\s+if=/dev/zero", "覆盖磁盘数据", "critical"),
    (r"mkfs\.", "格式化磁盘", "critical"),
    (r"fdisk", "磁盘分区操作", "critical"),
    (r"parted", "磁盘分区操作", "critical"),
    
    # Fork bomb
    (r":\(\)\{.*:\|:.*\}", "Fork bomb 攻击", "critical"),
    
    # 权限提升（中危）
    (r"^sudo\s+", "需要管理员权限", "high"),
    (r"^su\s+", "切换用户", "high"),
    (r"chmod\s+777", "设置过于宽松的权限", "high"),
    (r"systemctl", "系统服务管理", "high"),
    (r"service\s+", "系统服务管理", "high"),
    (r"reboot", "重启系统", "high"),
    (r"shutdown", "关闭系统", "high"),
    (r"poweroff", "关闭系统", "high"),
    
    # 网络危险（中危）
    (r"curl.*\|.*sh", "下载并执行脚本", "high"),
    (r"wget.*\|.*sh", "下载并执行脚本", "high"),
    (r"nc\s+-e", "反向 Shell", "high"),
    (r"ncat\s+-e", "反向 Shell", "high"),
    
    # 后台执行（低危）
    (r"&\s*$", "后台执行（建议使用 start_long_running_process）", "medium"),
    (r"nohup.*&", "后台持续运行", "medium"),
]


# 默认白名单（安全命令）
DEFAULT_WHITELIST = [
    # 版本控制
    r"^git\s+",
    r"^gh\s+",
    
    # 包管理（只读操作）
    r"^npm\s+(list|ls|view|info|search)",
    r"^pip\s+(list|show|search)",
    r"^poetry\s+(show|search)",
    
    # 测试和构建
    r"^pytest",
    r"^python\s+-m\s+pytest",
    r"^npm\s+(test|run\s+test)",
    r"^make\s+test",
    r"^cargo\s+test",
    
    # 查看命令（只读）
    r"^ls\s+",
    r"^cat\s+",
    r"^echo\s+",
    r"^pwd\s*$",
    r"^which\s+",
    r"^whereis\s+",
    r"^env\s*$",
    r"^printenv",
]


def check_shell_command(
    command: str,
    *,
    enabled: bool = True,
    level: str = "confirm",
    blacklist: List[str] = None,
    whitelist: List[str] = None,
    allow_sudo: bool = False,
) -> Tuple[bool, str, str]:
    """
    检查 Shell 命令是否安全
    
    Args:
        command: 要执行的命令
        enabled: 是否启用安全检查
        level: 安全级别（disabled/warning/confirm/deny）
        blacklist: 自定义黑名单（正则表达式列表）
        whitelist: 自定义白名单（正则表达式列表）
        allow_sudo: 是否允许 sudo 命令
    
    Returns:
        (is_safe, reason, severity)
        - is_safe: 是否安全
        - reason: 不安全的原因（如果安全则为空）
        - severity: 严重程度（critical/high/medium/low）
    """
    # 如果禁用检查，直接通过
    if not enabled or level == "disabled":
        return True, "", ""
    
    # 规范化命令（去除多余空格）
    command = " ".join(command.split())
    
    # 1. 检查白名单（优先）
    whitelist_patterns = whitelist or []
    whitelist_patterns.extend(DEFAULT_WHITELIST)
    
    for pattern in whitelist_patterns:
        try:
            if re.search(pattern, command, re.IGNORECASE):
                return True, "", ""
        except re.error:
            logger.warning(f"无效的白名单正则表达式: {pattern}")
    
    # 2. 检查黑名单
    blacklist_patterns = blacklist or []
    
    for pattern in blacklist_patterns:
        try:
            if re.search(pattern, command, re.IGNORECASE):
                return False, f"命令匹配黑名单模式: {pattern}", "critical"
        except re.error:
            logger.warning(f"无效的黑名单正则表达式: {pattern}")
    
    # 3. 检查默认危险模式
    for pattern, reason, severity in DEFAULT_DANGEROUS_PATTERNS:
        try:
            if re.search(pattern, command, re.IGNORECASE):
                # 特殊处理：如果允许 sudo，则跳过 sudo 检查
                if allow_sudo and "sudo" in pattern:
                    continue
                
                return False, reason, severity
        except re.error:
            logger.warning(f"无效的危险模式正则表达式: {pattern}")
    
    # 4. 所有检查通过
    return True, "", ""


def format_security_error(
    command: str,
    reason: str,
    severity: str,
    level: str = "confirm",
) -> str:
    """
    格式化安全错误消息
    
    Args:
        command: 被拦截的命令
        reason: 拦截原因
        severity: 严重程度
        level: 安全级别
    
    Returns:
        格式化的错误消息
    """
    severity_emoji = {
        "critical": "🚨",
        "high": "⚠️",
        "medium": "⚡",
        "low": "ℹ️",
    }
    
    emoji = severity_emoji.get(severity, "⚠️")
    
    if level == "deny":
        message = f"""{emoji} 危险命令被拒绝

命令: {command}
原因: {reason}
严重程度: {severity}

建议:
1. 使用更安全的 Miro 工具（如 delete_file 而非 rm）
2. 明确指定文件路径，避免通配符
3. 如果确实需要，请在配置中添加到白名单

配置方法:
在 miro_config.json 中添加:
{{
  "shell_security": {{
    "whitelist": ["{command}"]
  }}
}}

或设置安全级别为 warning:
{{
  "shell_security": {{
    "level": "warning"
  }}
}}
"""
    else:  # warning or confirm
        message = f"""{emoji} 警告：检测到潜在危险命令

命令: {command}
原因: {reason}
严重程度: {severity}

建议:
1. 仔细检查命令是否正确
2. 考虑使用更安全的 Miro 工具
3. 确保理解命令的后果

注意: 此命令将被执行，但已记录到日志。
"""
    
    return message


def get_shell_security_config():
    """
    从配置系统获取 Shell 安全配置
    
    Returns:
        配置字典
    """
    try:
        from backend.tools.coding.foundation.core_mechanisms.config import config
        
        return {
            "enabled": config.get("shell_security.enabled", True),
            "level": config.get("shell_security.level", "confirm"),
            "blacklist": config.get("shell_security.blacklist", []),
            "whitelist": config.get("shell_security.whitelist", []),
            "allow_sudo": config.get("shell_security.allow_sudo", False),
        }
    except Exception as e:
        logger.warning(f"无法读取 Shell 安全配置: {e}，使用默认值")
        return {
            "enabled": True,
            "level": "confirm",
            "blacklist": [],
            "whitelist": [],
            "allow_sudo": False,
        }


__all__ = [
    "ShellSecurityError",
    "check_shell_command",
    "format_security_error",
    "get_shell_security_config",
    "DEFAULT_DANGEROUS_PATTERNS",
    "DEFAULT_WHITELIST",
]
