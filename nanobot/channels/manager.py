"""用于协调各类聊天渠道的渠道管理器。"""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import Config

if TYPE_CHECKING:
    from nanobot.session.manager import SessionManager


class ChannelManager:
    """
    管理各类聊天渠道并协调消息路由。
    
    核心职责：
    - 初始化已启用的渠道（电报、微信等）
    - 启动/停止渠道服务
    - 路由出站消息到对应渠道
    """
    
    def __init__(self, config: Config, bus: MessageBus, session_manager: "SessionManager | None" = None):
        self.config = config
        self.bus = bus
        self.session_manager = session_manager
        self.channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task | None = None
        
        self._init_channels()
    
    def _init_channels(self) -> None:
        """根据配置初始化各类渠道。"""
        
        # Telegram（电报）渠道
        if self.config.channels.telegram.enabled:
            try:
                from nanobot.channels.telegram import TelegramChannel
                self.channels["telegram"] = TelegramChannel(
                    self.config.channels.telegram,
                    self.bus,
                    groq_api_key=self.config.providers.groq.api_key,
                    session_manager=self.session_manager,
                )
                logger.info("Telegram渠道已启用")
            except ImportError as e:
                logger.warning(f"Telegram渠道不可用：{e}")
        
        # WhatsApp（微信）渠道
        if self.config.channels.whatsapp.enabled:
            try:
                from nanobot.channels.whatsapp import WhatsAppChannel
                self.channels["whatsapp"] = WhatsAppChannel(
                    self.config.channels.whatsapp, self.bus
                )
                logger.info("WhatsApp渠道已启用")
            except ImportError as e:
                logger.warning(f"WhatsApp渠道不可用：{e}")

        # Discord渠道
        if self.config.channels.discord.enabled:
            try:
                from nanobot.channels.discord import DiscordChannel
                self.channels["discord"] = DiscordChannel(
                    self.config.channels.discord, self.bus
                )
                logger.info("Discord渠道已启用")
            except ImportError as e:
                logger.warning(f"Discord渠道不可用：{e}")
        
        # 飞书渠道
        if self.config.channels.feishu.enabled:
            try:
                from nanobot.channels.feishu import FeishuChannel
                self.channels["feishu"] = FeishuChannel(
                    self.config.channels.feishu, self.bus
                )
                logger.info("飞书渠道已启用")
            except ImportError as e:
                logger.warning(f"飞书渠道不可用：{e}")

        # 钉钉渠道
        if self.config.channels.dingtalk.enabled:
            try:
                from nanobot.channels.dingtalk import DingTalkChannel
                self.channels["dingtalk"] = DingTalkChannel(
                    self.config.channels.dingtalk, self.bus
                )
                logger.info("钉钉渠道已启用")
            except ImportError as e:
                logger.warning(f"钉钉渠道不可用：{e}")

        # 邮件渠道
        if self.config.channels.email.enabled:
            try:
                from nanobot.channels.email import EmailChannel
                self.channels["email"] = EmailChannel(
                    self.config.channels.email, self.bus
                )
                logger.info("邮件渠道已启用")
            except ImportError as e:
                logger.warning(f"邮件渠道不可用：{e}")
    
    async def _start_channel(self, name: str, channel: BaseChannel) -> None:
        """启动单个渠道，并记录启动过程中的异常。"""
        try:
            await channel.start()
        except Exception as e:
            logger.error(f"启动{name}渠道失败：{e}")

    async def start_all(self) -> None:
        """启动所有渠道和出站消息分发器。"""
        if not self.channels:
            logger.warning("未启用任何渠道")
            return
        
        # 启动出站消息分发器
        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())
        
        # 启动所有渠道
        tasks = []
        for name, channel in self.channels.items():
            logger.info(f"正在启动{name}渠道...")
            tasks.append(asyncio.create_task(self._start_channel(name, channel)))
        
        # 等待所有渠道启动完成（渠道本身应长期运行）
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def stop_all(self) -> None:
        """停止所有渠道和消息分发器。"""
        logger.info("正在停止所有渠道...")
        
        # 停止消息分发器
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
        
        # 停止所有渠道
        for name, channel in self.channels.items():
            try:
                await channel.stop()
                logger.info(f"{name}渠道已停止")
            except Exception as e:
                logger.error(f"停止{name}渠道时出错：{e}")
    
    async def _dispatch_outbound(self) -> None:
        """将出站消息分发到对应的渠道。"""
        logger.info("出站消息分发器已启动")
        
        while True:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_outbound(),
                    timeout=1.0
                )
                
                channel = self.channels.get(msg.channel)
                if channel:
                    try:
                        await channel.send(msg)
                    except Exception as e:
                        logger.error(f"向{msg.channel}渠道发送消息失败：{e}")
                else:
                    logger.warning(f"未知渠道：{msg.channel}")
                    
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
    
    def get_channel(self, name: str) -> BaseChannel | None:
        """根据名称获取指定渠道实例。"""
        return self.channels.get(name)
    
    def get_status(self) -> dict[str, Any]:
        """获取所有渠道的运行状态。"""
        return {
            name: {
                "enabled": True,
                "running": channel.is_running
            }
            for name, channel in self.channels.items()
        }
    
    @property
    def enabled_channels(self) -> list[str]:
        """获取已启用渠道的名称列表。"""
        return list(self.channels.keys())