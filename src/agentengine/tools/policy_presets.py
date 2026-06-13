"""Pre-built :class:`ExecPolicy` configurations for common deployment modes.

These align with Claude Code and Codex CLI default safety postures:
sandboxed agents get a permissive default (the container is the security
boundary), while host-level agents get destructive-command blocking.
"""

from __future__ import annotations

from agentengine.tools.policy import ExecPolicy, ExecPolicyAction, ExecPolicyRule


# Policy for sandboxed agents — the container is the real security boundary,
# so the policy only blocks obviously malicious patterns that even sandboxed
# execution should refuse.
SANDBOX_DEFAULT = ExecPolicy(
    rules=[
        ExecPolicyRule(
            ("rm -rf /", "rm -rf /*", "rm -rf ~", "rm -rf ~/"),
            ExecPolicyAction.DENY,
            "拒绝递归删除根目录或家目录",
        ),
        ExecPolicyRule(
            "mkfs.",
            ExecPolicyAction.DENY,
            "拒绝格式化文件系统",
        ),
        ExecPolicyRule(
            "dd if=",
            ExecPolicyAction.DENY,
            "拒绝裸磁盘写入",
        ),
        ExecPolicyRule(
            ":(){ :|:& };:",
            ExecPolicyAction.DENY,
            "拒绝 fork bomb",
        ),
        ExecPolicyRule(
            ("shutdown", "reboot", "halt", "poweroff"),
            ExecPolicyAction.DENY,
            "拒绝关机/重启",
        ),
        ExecPolicyRule(
            "chmod 777 /",
            ExecPolicyAction.DENY,
            "拒绝全系统权限放宽",
        ),
        ExecPolicyRule(
            "curl", ExecPolicyAction.ALLOW,
            "允许 curl（沙盒无网络，实际无效）",
        ),
        ExecPolicyRule(
            "wget", ExecPolicyAction.ALLOW,
            "允许 wget（沙盒无网络，实际无效）",
        ),
    ],
    default=ExecPolicyAction.ALLOW,
)


# Policy for host-level execution — more conservative since there is no
# container boundary. Blocks destructive patterns and requires approval for
# high-risk operations.
HOST_DEFAULT = ExecPolicy(
    rules=[
        ExecPolicyRule(
            ("rm -rf /", "rm -rf /*", "rm -rf ~", "rm -rf ~/"),
            ExecPolicyAction.DENY,
            "拒绝递归删除根目录或家目录",
        ),
        ExecPolicyRule("mkfs.", ExecPolicyAction.DENY, "拒绝格式化文件系统"),
        ExecPolicyRule("dd if=", ExecPolicyAction.DENY, "拒绝裸磁盘写入"),
        ExecPolicyRule(":(){ :|:& };:", ExecPolicyAction.DENY, "拒绝 fork bomb"),
        ExecPolicyRule(
            ("shutdown", "reboot", "halt", "poweroff"),
            ExecPolicyAction.DENY,
            "拒绝关机/重启",
        ),
        ExecPolicyRule(
            "chmod 777",
            ExecPolicyAction.DENY,
            "拒绝权限放宽",
        ),
        # High-risk git operations require approval
        ExecPolicyRule(
            "git push --force",
            ExecPolicyAction.ASK,
            "强制推送需要确认",
        ),
        ExecPolicyRule(
            "git push -f",
            ExecPolicyAction.ASK,
            "强制推送需要确认",
        ),
        ExecPolicyRule(
            "git reset --hard",
            ExecPolicyAction.ASK,
            "硬重置需要确认",
        ),
        ExecPolicyRule(
            "git clean -f",
            ExecPolicyAction.ASK,
            "清理未跟踪文件需要确认",
        ),
        ExecPolicyRule(
            "pip install",
            ExecPolicyAction.ASK,
            "安装 Python 包需要确认",
        ),
        ExecPolicyRule(
            "npm install -g",
            ExecPolicyAction.ASK,
            "全局安装 npm 包需要确认",
        ),
    ],
    default=ExecPolicyAction.ALLOW,
)


__all__ = ["HOST_DEFAULT", "SANDBOX_DEFAULT"]
