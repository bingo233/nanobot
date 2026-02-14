"""基于 lark-oapi SDK 并使用 WebSocket 长连接的飞书/Lark 频道实现。"""

import asyncio
import json
import re
import threading
from collections import OrderedDict
from typing import Any

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import FeishuConfig

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        Emoji,
        P2ImMessageReceiveV1,
    )
    FEISHU_AVAILABLE = True
except ImportError:
    FEISHU_AVAILABLE = False
    lark = None
    Emoji = None

# 消息类型显示映射
MSG_TYPE_MAP = {
    "image": "[图片]",
    "audio": "[音频]",
    "file": "[文件]",
    "sticker": "[表情包]",
}


class FeishuChannel(BaseChannel):
    """
    基于 WebSocket 长连接的飞书/Lark 频道实现。
    
    使用 WebSocket 接收事件 - 无需公网 IP 或 Webhook。
    
    必要条件:
    - 飞书开放平台的 App ID 和 App Secret
    - 已启用机器人能力
    - 已启用事件订阅 (im.message.receive_v1)
    """
    
    name = "feishu"
    
    def __init__(self, config: FeishuConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: FeishuConfig = config
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()  # 有序的去重缓存
        self._loop: asyncio.AbstractEventLoop | None = None
    
    async def start(self) -> None:
        """启动飞书机器人（使用 WebSocket 长连接）。"""
        if not FEISHU_AVAILABLE:
            logger.error("飞书 SDK 未安装。请执行: pip install lark-oapi")
            return
        
        if not self.config.app_id or not self.config.app_secret:
            logger.error("飞书 app_id 和 app_secret 未配置")
            return
        
        self._running = True
        self._loop = asyncio.get_running_loop()
        
        # 创建用于发送消息的 Lark 客户端
        self._client = lark.Client.builder() \
            .app_id(self.config.app_id) \
            .app_secret(self.config.app_secret) \
            .log_level(lark.LogLevel.INFO) \
            .build()
        
        # 创建事件处理器（仅注册消息接收事件，忽略其他事件）
        event_handler = lark.EventDispatcherHandler.builder(
            self.config.encrypt_key or "",
            self.config.verification_token or "",
        ).register_p2_im_message_receive_v1(
            self._on_message_sync
        ).build()
        
        # 创建用于长连接的 WebSocket 客户端
        self._ws_client = lark.ws.Client(
            self.config.app_id,
            self.config.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO
        )
        
        # 在独立线程中启动 WebSocket 客户端
        def run_ws():
            try:
                self._ws_client.start()
            except Exception as e:
                logger.error(f"飞书 WebSocket 错误: {e}")
        
        self._ws_thread = threading.Thread(target=run_ws, daemon=True)
        self._ws_thread.start()
        
        logger.info("飞书机器人已通过 WebSocket 长连接启动")
        logger.info("无需公网 IP - 使用 WebSocket 接收事件")
        
        # 保持运行直到停止
        while self._running:
            await asyncio.sleep(1)
    
    async def stop(self) -> None:
        """停止飞书机器人。"""
        self._running = False
        if self._ws_client:
            try:
                self._ws_client.stop()
            except Exception as e:
                logger.warning(f"停止 WebSocket 客户端时出错: {e}")
        logger.info("飞书机器人已停止")
    
    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> None:
        """添加消息表情回应的同步辅助方法（运行在线程池中）。"""
        try:
            request = CreateMessageReactionRequest.builder() \
                .message_id(message_id) \
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                ).build()
            
            response = self._client.im.v1.message_reaction.create(request)
            
            if not response.success():
                logger.warning(f"添加表情回应失败: 错误码={response.code}, 错误信息={response.msg}")
            else:
                logger.debug(f"已为消息 {message_id} 添加 {emoji_type} 表情回应")
        except Exception as e:
            logger.warning(f"添加表情回应时出错: {e}")

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """
        为消息添加表情回应（非阻塞）。
        
        常用表情类型: THUMBSUP(点赞), OK(好的), EYES(查看), DONE(完成), OnIt(处理中), HEART(爱心)
        """
        if not self._client or not Emoji:
            return
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._add_reaction_sync, message_id, emoji_type)
    
    # 匹配 markdown 表格的正则表达式（表头 + 分隔线 + 数据行）
    _TABLE_RE = re.compile(
        r"((?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|[-:\s|]+\|[ \t]*\n)(?:^[ \t]*\|.+\|[ \t]*\n?)+)",
        re.MULTILINE,
    )

    @staticmethod
    def _parse_md_table(table_text: str) -> dict | None:
        """将 markdown 表格解析为飞书表格元素。"""
        lines = [l.strip() for l in table_text.strip().split("\n") if l.strip()]
        if len(lines) < 3:
            return None
        split = lambda l: [c.strip() for c in l.strip("|").split("|")]
        headers = split(lines[0])
        rows = [split(l) for l in lines[2:]]
        columns = [{"tag": "column", "name": f"c{i}", "display_name": h, "width": "auto"}
                   for i, h in enumerate(headers)]
        return {
            "tag": "table",
            "page_size": len(rows) + 1,
            "columns": columns,
            "rows": [{f"c{i}": r[i] if i < len(r) else "" for i in range(len(headers))} for r in rows],
        }

    def _build_card_elements(self, content: str) -> list[dict]:
        """将内容拆分为 markdown + 表格元素，用于飞书卡片消息。"""
        elements, last_end = [], 0
        for m in self._TABLE_RE.finditer(content):
            before = content[last_end:m.start()].strip()
            if before:
                elements.append({"tag": "markdown", "content": before})
            elements.append(self._parse_md_table(m.group(1)) or {"tag": "markdown", "content": m.group(1)})
            last_end = m.end()
        remaining = content[last_end:].strip()
        if remaining:
            elements.append({"tag": "markdown", "content": remaining})
        return elements or [{"tag": "markdown", "content": content}]

    async def send(self, msg: OutboundMessage) -> None:
        """通过飞书发送消息。"""
        if not self._client:
            logger.warning("飞书客户端未初始化")
            return
        
        try:
            # 根据 chat_id 格式确定 receive_id_type
            # open_id 以 "ou_" 开头, chat_id 以 "oc_" 开头
            if msg.chat_id.startswith("oc_"):
                receive_id_type = "chat_id"
            else:
                receive_id_type = "open_id"
            
            # 构建支持 markdown + 表格的卡片消息
            elements = self._build_card_elements(msg.content)
            card = {
                "config": {"wide_screen_mode": True},
                "elements": elements,
            }
            content = json.dumps(card, ensure_ascii=False)
            
            request = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(msg.chat_id)
                    .msg_type("interactive")
                    .content(content)
                    .build()
                ).build()
            
            response = self._client.im.v1.message.create(request)
            
            if not response.success():
                logger.error(
                    f"发送飞书消息失败: 错误码={response.code}, "
                    f"错误信息={response.msg}, 日志ID={response.get_log_id()}"
                )
            else:
                logger.debug(f"飞书消息已发送至 {msg.chat_id}")
                
        except Exception as e:
            logger.error(f"发送飞书消息时出错: {e}")
    
    def _on_message_sync(self, data: "P2ImMessageReceiveV1") -> None:
        """
        处理入站消息的同步处理器（从 WebSocket 线程调用）。
        在主事件循环中调度异步处理逻辑。
        """
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message(data), self._loop)
    
    async def _on_message(self, data: "P2ImMessageReceiveV1") -> None:
        """处理来自飞书的入站消息。"""
        try:
            event = data.event
            message = event.message
            sender = event.sender
            
            # 去重检查
            message_id = message.message_id
            if message_id in self._processed_message_ids:
                return
            self._processed_message_ids[message_id] = None
            
            # 修剪缓存: 当超过 1000 条时保留最近的 500 条
            while len(self._processed_message_ids) > 1000:
                self._processed_message_ids.popitem(last=False)
            
            # 跳过机器人发送的消息
            sender_type = sender.sender_type
            if sender_type == "bot":
                return
            
            sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            chat_id = message.chat_id
            chat_type = message.chat_type  # "p2p" (单聊) 或 "group" (群聊)
            msg_type = message.message_type
            
            # 添加表情回应表示"已读"
            await self._add_reaction(message_id, "THUMBSUP")
            
            # 解析消息内容
            if msg_type == "text":
                try:
                    content = json.loads(message.content).get("text", "")
                except json.JSONDecodeError:
                    content = message.content or ""
            else:
                content = MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]")
            
            if not content:
                return
            
            # 转发到消息总线
            reply_to = chat_id if chat_type == "group" else sender_id
            await self._handle_message(
                sender_id=sender_id,
                chat_id=reply_to,
                content=content,
                metadata={
                    "message_id": message_id,
                    "chat_type": chat_type,
                    "msg_type": msg_type,
                }
            )
            
        except Exception as e:
            logger.error(f"处理飞书消息时出错: {e}")