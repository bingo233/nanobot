"""聊天平台的基础通道接口。"""

from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus


class BaseChannel(ABC):
    """
    聊天通道实现的抽象基类。
    
    每个通道（Telegram、Discord 等）都应实现此接口，
    以集成到 nanobot 消息总线中。
    """
    
    name: str = "base"
    
    def __init__(self, config: Any, bus: MessageBus):
        """
        初始化通道。
        
        参数:
            config: 通道专属的配置项。
            bus: 用于通信的消息总线。
        """
        self.config = config
        self.bus = bus
        self._running = False
    
    @abstractmethod
    async def start(self) -> None:
        """
        启动通道并开始监听消息。
        
        这应该是一个长期运行的异步任务，需完成：
        1. 连接到聊天平台
        2. 监听入站消息
        3. 通过 _handle_message() 将消息转发到消息总线
        """
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """停止通道并清理资源。"""
        pass
    
    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """
        通过此通道发送消息。
        
        参数:
            msg: 要发送的消息。
        """
        pass
    
    def is_allowed(self, sender_id: str) -> bool:
        """
        检查发送者是否有权限使用此机器人。
        
        参数:
            sender_id: 发送者的唯一标识。
        
        返回:
            有权限时返回 True，否则返回 False。
        """
        allow_list = getattr(self.config, "allow_from", [])
        
        # 如果没有配置允许列表，则允许所有发送者
        if not allow_list:
            return True
        
        sender_str = str(sender_id)
        if sender_str in allow_list:
            return True
        if "|" in sender_str:
            for part in sender_str.split("|"):
                if part and part in allow_list:
                    return True
        return False
    
    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None
    ) -> None:
        """
        处理来自聊天平台的入站消息。
        
        此方法会检查权限，并将消息转发到消息总线。
        
        参数:
            sender_id: 发送者的唯一标识。
            chat_id: 聊天/频道的唯一标识。
            content: 消息文本内容。
            media: 可选的媒体 URL 列表。
            metadata: 可选的通道专属元数据。
        """
        if not self.is_allowed(sender_id):
            logger.warning(
                f"通道 {self.name} 拒绝发送者 {sender_id} 的访问请求。 "
                f"如需授权，请将其添加到配置文件的 allowFrom 列表中。"
            )
            return
        
        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=metadata or {}
        )
        
        await self.bus.publish_inbound(msg)
    
    @property
    def is_running(self) -> bool:
        """检查通道是否处于运行状态。"""
        return self._running