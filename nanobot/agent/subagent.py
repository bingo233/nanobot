"""后台子代理任务执行管理器。"""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, ListDirTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool


class SubagentManager:
    """
    管理后台子代理的执行流程。
    
    子代理是运行在后台的轻量级代理实例，用于处理特定任务。
    它们共享同一个大语言模型（LLM）提供器，但拥有独立的上下文环境
    和针对性的系统提示词。
    """
    
    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        model: str | None = None,
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        restrict_to_workspace: bool = False,
    ):
        from nanobot.config.schema import ExecToolConfig
        self.provider = provider  # LLM模型提供器
        self.workspace = workspace  # 工作目录路径
        self.bus = bus  # 消息总线实例
        self.model = model or provider.get_default_model()  # 使用的模型名称
        self.brave_api_key = brave_api_key  # Brave搜索API密钥
        self.exec_config = exec_config or ExecToolConfig()  # 执行工具配置
        self.restrict_to_workspace = restrict_to_workspace  # 是否限制操作范围至工作目录
        self._running_tasks: dict[str, asyncio.Task[None]] = {}  # 运行中的任务字典（任务ID -> 异步任务）
    
    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
    ) -> str:
        """
        启动一个子代理在后台执行指定任务。
        
        参数:
            task: 子代理需要执行的任务描述。
            label: 可选的、人类可读的任务标签。
            origin_channel: 用于发布任务结果的渠道。
            origin_chat_id: 用于发布任务结果的聊天ID。
        
        返回:
            表示子代理已启动的状态提示信息。
        """
        task_id = str(uuid.uuid4())[:8]  # 生成8位短任务ID
        # 构建显示标签（过长则截断）
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        
        # 任务结果推送的目标位置
        origin = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
        }
        
        # 创建后台异步任务
        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin)
        )
        self._running_tasks[task_id] = bg_task
        
        # 任务完成后自动清理字典中的引用
        bg_task.add_done_callback(lambda _: self._running_tasks.pop(task_id, None))
        
        logger.info(f"已启动子代理 [{task_id}]: {display_label}")
        return f"子代理 [{display_label}] 已启动 (ID: {task_id})。任务完成后我会通知你。"
    
    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
    ) -> None:
        """执行子代理任务并发布执行结果。"""
        logger.info(f"子代理 [{task_id}] 开始执行任务: {label}")
        
        try:
            # 构建子代理可用工具（不含消息发送工具和子代理生成工具）
            tools = ToolRegistry()
            # 限制文件操作范围（如果开启）
            allowed_dir = self.workspace if self.restrict_to_workspace else None
            tools.register(ReadFileTool(allowed_dir=allowed_dir))  # 读取文件工具
            tools.register(WriteFileTool(allowed_dir=allowed_dir))  # 写入文件工具
            tools.register(ListDirTool(allowed_dir=allowed_dir))  # 列出目录工具
            # 执行shell命令工具
            tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
            ))
            tools.register(WebSearchTool(api_key=self.brave_api_key))  # 网页搜索工具
            tools.register(WebFetchTool())  # 网页内容获取工具
            
            # 构建包含子代理专属提示词的消息列表
            system_prompt = self._build_subagent_prompt(task)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},  # 系统提示词
                {"role": "user", "content": task},  # 用户任务指令
            ]
            
            # 运行代理循环（限制最大迭代次数防止无限循环）
            max_iterations = 15
            iteration = 0
            final_result: str | None = None
            
            while iteration < max_iterations:
                iteration += 1
                
                # 调用LLM获取响应（包含工具调用指令）
                response = await self.provider.chat(
                    messages=messages,
                    tools=tools.get_definitions(),
                    model=self.model,
                )
                
                if response.has_tool_calls:
                    # 将工具调用指令添加到消息历史（助理角色消息）
                    tool_call_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in response.tool_calls
                    ]
                    messages.append({
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": tool_call_dicts,
                    })
                    
                    # 执行所有工具调用
                    for tool_call in response.tool_calls:
                        args_str = json.dumps(tool_call.arguments)
                        logger.debug(f"子代理 [{task_id}] 执行工具: {tool_call.name}，参数: {args_str}")
                        # 执行工具并获取结果
                        result = await tools.execute(tool_call.name, tool_call.arguments)
                        # 将工具执行结果添加到消息历史（工具角色消息）
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "content": result,
                        })
                else:
                    # 无工具调用时，将LLM直接响应作为最终结果
                    final_result = response.content
                    break  # 退出循环
                
            # 处理最大迭代次数耗尽但未生成最终结果的情况
            if final_result is None:
                final_result = "任务已执行完毕，但未生成最终响应内容。"
            
            logger.info(f"子代理 [{task_id}] 任务执行成功")
            # 发布执行成功的结果
            await self._announce_result(task_id, label, task, final_result, origin, "ok")
            
        except Exception as e:
            # 捕获所有异常并记录错误信息
            error_msg = f"错误信息: {str(e)}"
            logger.error(f"子代理 [{task_id}] 执行失败: {e}")
            # 发布执行失败的结果
            await self._announce_result(task_id, label, task, error_msg, origin, "error")
    
    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """通过消息总线将子代理执行结果通知给主代理。"""
        # 构建状态描述文本
        status_text = "执行成功" if status == "ok" else "执行失败"
        
        # 构建推送内容（供主代理整理后回复用户）
        announce_content = f"""[子代理'{label}' {status_text}]

任务描述: {task}

执行结果:
{result}

请将以上内容用自然语言简洁总结后回复用户（1-2句话即可）。
不要提及「子代理」「任务ID」等技术细节。"""
        
        # 封装为系统消息触发主代理处理
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
        )
        
        # 发布消息到总线
        await self.bus.publish_inbound(msg)
        logger.debug(f"子代理 [{task_id}] 已将结果推送至 {origin['channel']}:{origin['chat_id']}")
    
    def _build_subagent_prompt(self, task: str) -> str:
        """为子代理构建针对性的系统提示词。"""
        return f"""# 子代理执行规则

你是由主代理启动的专用子代理，仅负责完成指定任务。

## 核心任务
{task}

## 执行规则
1. 聚焦任务 - 仅完成分配的任务，不执行其他无关操作
2. 你的最终响应将被反馈给主代理
3. 不主动发起对话，不承接额外的附带任务
4. 发现和结论需简洁且信息完整

## 可执行操作
- 读取和写入工作目录内的文件
- 执行shell命令
- 搜索网页并获取网页内容
- 全面完成分配的任务

## 禁止操作
- 直接向用户发送消息（无消息发送工具可用）
- 生成其他子代理
- 访问主代理的对话历史

## 工作目录
你的工作目录路径为: {self.workspace}

任务完成后，请提供清晰的结果总结或操作记录。"""
    
    def get_running_count(self) -> int:
        """返回当前正在运行的子代理数量。"""
        return len(self._running_tasks)