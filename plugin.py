"""静音用户插件：指定 QQ 号的消息只当背景，机器人永远不回复。

做法：消息在 `chat.receive.after_process` 阶段被拦下（abort），不进入消息主链路，
所以不会触发回复、不会占 @ 强制触发、也不会被 reply 工具选中；同时把这条消息
用 `maisaka.context.append` 塞进该会话的 Maisaka 上下文，机器人仍能在历史里看到它。

这样整条路径零 token：不生成、不重试、不删回复。
"""

from __future__ import annotations

import logging
from typing import Any

from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import HookMode, HookOrder


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="1.0.0", description="配置版本")


class MuteUserConfig(PluginConfigBase):
    """静音用户配置。"""

    __ui_label__ = "静音用户"
    __ui_icon__ = "user-x"
    __ui_order__ = 1

    users: list[str] = Field(
        default_factory=list,
        description="不回复的 QQ 号；他们的消息只作为背景上下文，机器人不会回",
    )
    background_prefix: str = Field(
        default="[背景]",
        description="插入上下文时的前缀，方便模型区分“这条只作背景”",
    )


class MuteUserPluginConfig(PluginConfigBase):
    """静音用户插件配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    mute_user: MuteUserConfig = Field(default_factory=MuteUserConfig)


class MuteUserPlugin(MaiBotPlugin):
    """指定 QQ 号只当背景、永不回复。"""

    config_model = MuteUserPluginConfig

    def __init__(self) -> None:
        super().__init__()
        self._users: set[str] = set()

    async def on_load(self) -> None:
        self._refresh_config()
        self._logger().info("静音用户插件已加载：静音号=%s", sorted(self._users))

    async def on_unload(self) -> None:
        return None

    async def on_config_update(self, scope: str = "", config_data: Any = None, version: Any = None) -> None:
        del scope, config_data, version
        self._refresh_config()

    def _refresh_config(self) -> None:
        self._users = {str(u).strip() for u in self.config.mute_user.users if str(u).strip()}

    def _logger(self) -> logging.Logger:
        try:
            return self.ctx.logger
        except Exception:  # noqa: BLE001
            return logging.getLogger("plugin.dsh.mute-user")

    @HookHandler(
        "chat.receive.after_process",
        name="mute_user_gate",
        description="静音用户的消息只当背景，不进入回复链路",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
    )
    async def on_message(self, message: Any = None, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        if not self.config.plugin.enabled or not isinstance(message, dict):
            return {"action": "continue"}

        info = message.get("message_info") or {}
        user = info.get("user_info") or {}
        user_id = str(user.get("user_id") or "")
        if not user_id or user_id not in self._users:
            return {"action": "continue"}

        session_id = str(message.get("session_id") or "")
        if session_id:
            await self._append_background(message, user, session_id)

        # 拦下消息：不进主链路，不触发、不回复、不能被 reply 工具选中
        return {"action": "abort"}

    async def _append_background(self, message: dict[str, Any], user: dict[str, Any], session_id: str) -> None:
        user_id = str(user.get("user_id") or "")
        nickname = str(user.get("user_cardname") or user.get("user_nickname") or user_id)
        text = str(message.get("processed_plain_text") or "").strip()
        prefix = str(self.config.mute_user.background_prefix or "[背景]")
        visible_text = f"{prefix} {nickname}({user_id})：{text}" if text else f"{prefix} {nickname}({user_id})"

        segments = message.get("raw_message")
        if not isinstance(segments, list) or not segments:
            segments = [{"type": "text", "data": text or "[消息]"}]

        appended = await self._try_append(session_id, segments, visible_text)
        if appended:
            return
        # 新会话第一次来消息时聊天流还没建出来，先开一个再塞
        try:
            await self._ensure_session(message, user)
        except Exception as exc:  # noqa: BLE001
            self._logger().warning("静音用户消息建会话失败 session=%s: %s", session_id, exc)
            return
        if not await self._try_append(session_id, segments, visible_text):
            self._logger().warning("静音用户消息写入背景上下文失败 session=%s", session_id)

    async def _try_append(self, session_id: str, segments: list[Any], visible_text: str) -> bool:
        try:
            await self.ctx.maisaka.context.append(
                session_id,
                segments,
                visible_text=visible_text,
                source_kind="plugin:dsh.mute-user",
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _ensure_session(self, message: dict[str, Any], user: dict[str, Any]) -> None:
        info = message.get("message_info") or {}
        group = info.get("group_info") or {}
        group_id = str(group.get("group_id") or "")
        user_id = str(user.get("user_id") or "")
        platform = str(message.get("platform") or "qq")
        if group_id:
            await self.ctx.chat.open_session(
                platform,
                "group",
                group_id=group_id,
                account_id=str(message.get("account_id") or ""),
                scope=str(message.get("scope") or ""),
            )
        else:
            await self.ctx.chat.open_session(
                platform,
                "private",
                user_id=user_id,
                account_id=str(message.get("account_id") or ""),
                scope=str(message.get("scope") or ""),
            )


def create_plugin() -> MuteUserPlugin:
    """插件工厂函数，供宿主加载。"""
    return MuteUserPlugin()
