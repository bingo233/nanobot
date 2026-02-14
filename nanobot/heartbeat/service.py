"""心跳服务 - 定期唤醒智能代理以检查待处理任务。"""

import asyncio
from pathlib import Path
from typing import Any, Callable, Coroutine

from loguru import logger

# 默认心跳间隔：30分钟
DEFAULT_HEARTBEAT_INTERVAL_S = 30 * 60

# 心跳触发时发送给代理的提示语
HEARTBEAT_PROMPT = """读取工作区中的 HEARTBEAT.md 文件（如果存在）。
遵循文件中列出的所有指令或执行相关任务。
如果没有需要处理的事项，仅回复：HEARTBEAT_OK"""

# 表示"无任务需处理"的标识
HEARTBEAT_OK_TOKEN = "HEARTBEAT_OK"


def _is_heartbeat_empty(content: str | None) -> bool:
    """检查 HEARTBEAT.md 是否包含可执行的内容。"""
    if not content:
        return True
    
    # 需跳过的行：空行、标题行、HTML注释、空复选框
    skip_patterns = {"- [ ]", "* [ ]", "- [x]", "* [x]"}
    
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("<!--") or line in skip_patterns:
            continue
        return False  # 发现可执行内容
    
    return True


class HeartbeatService:
    """
    周期性心跳服务，用于唤醒智能代理检查待处理任务。
    
    代理会读取工作区中的 HEARTBEAT.md 文件，并执行其中列出的
    所有任务。如果没有需要处理的事项，代理会回复 HEARTBEAT_OK。
    """
    
    def __init__(
        self,
        workspace: Path,
        on_heartbeat: Callable[[str], Coroutine[Any, Any, str]] | None = None,
        interval_s: int = DEFAULT_HEARTBEAT_INTERVAL_S,
        enabled: bool = True,
    ):
        self.workspace = workspace  # 工作区路径
        self.on_heartbeat = on_heartbeat  # 心跳触发时的回调函数
        self.interval_s = interval_s  # 心跳间隔（秒）
        self.enabled = enabled  # 是否启用心跳服务
        self._running = False  # 服务运行状态
        self._task: asyncio.Task | None = None  # 心跳循环任务
    
    @property
    def heartbeat_file(self) -> Path:
        """获取 HEARTBEAT.md 文件的路径。"""
        return self.workspace / "HEARTBEAT.md"
    
    def _read_heartbeat_file(self) -> str | None:
        """读取 HEARTBEAT.md 文件的内容。"""
        if self.heartbeat_file.exists():
            try:
                return self.heartbeat_file.read_text()
            except Exception:
                return None
        return None
    
    async def start(self) -> None:
        """启动心跳服务。"""
        if not self.enabled:
            logger.info("心跳服务已禁用")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"心跳服务已启动（每 {self.interval_s} 秒执行一次）")
    
    def stop(self) -> None:
        """停止心跳服务。"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
    
    async def _run_loop(self) -> None:
        """心跳服务主循环。"""
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳服务执行错误: {e}")
    
    async def _tick(self) -> None:
        """执行单次心跳检查。"""
        content = self._read_heartbeat_file()
        
        # 如果 HEARTBEAT.md 为空或不存在，则跳过
        if _is_heartbeat_empty(content):
            logger.debug("心跳检查：无待处理任务（HEARTBEAT.md 为空）")
            return
        
        logger.info("心跳检查：正在检查待处理任务...")
        
        if self.on_heartbeat:
            try:
                response = await self.on_heartbeat(HEARTBEAT_PROMPT)
                
                # 检查代理是否回复"无任务需处理"
                if HEARTBEAT_OK_TOKEN.replace("_", "") in response.upper().replace("_", ""):
                    logger.info("心跳检查：完成（无需执行任何操作）")
                else:
                    logger.info(f"心跳检查：任务执行完成")
                    
            except Exception as e:
                logger.error(f"心跳任务执行失败: {e}")
    
    async def trigger_now(self) -> str | None:
        """手动触发一次心跳检查。"""
        if self.on_heartbeat:
            return await self.on_heartbeat(HEARTBEAT_PROMPT)
        return None