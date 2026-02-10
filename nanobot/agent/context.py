"""用于组装代理提示词的上下文构建器。"""

import base64
import mimetypes
import platform
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader


class ContextBuilder:
    """
    为代理构建上下文（系统提示词 + 消息列表）。
    
    将引导文件、记忆数据、技能信息和对话历史
    组装成连贯的提示词供大语言模型（LLM）使用。
    """
    
    # 引导文件列表
    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)
    
    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """
        从引导文件、记忆数据和技能信息构建系统提示词。
        
        参数：
            skill_names: 可选的技能名称列表，用于指定要包含的技能。
        
        返回：
            完整的系统提示词字符串。
        """
        parts = []
        
        # 核心身份信息
        parts.append(self._get_identity())
        
        # 引导文件内容
        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)
        
        # 记忆上下文
        memory = self.memory.get_memory_context()
        if memory:
            parts.append(f"# 记忆\n\n{memory}")
        
        # 技能 - 渐进式加载
        # 1. 始终加载的技能：包含完整内容
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# 活跃技能\n\n{always_content}")
        
        # 2. 可用技能：仅展示摘要（代理使用read_file工具加载完整内容）
        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(f"""# 技能

以下技能扩展了你的能力范围。要使用某个技能，请通过read_file工具读取其SKILL.md文件。
标记为available="false"的技能需要先安装依赖项 - 你可以尝试使用apt/brew命令安装。

{skills_summary}""")
        
        return "\n\n---\n\n".join(parts)
    
    def _get_identity(self) -> str:
        """获取核心身份信息部分。"""
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"
        
        return f"""# nanobot 🐈

你是nanobot，一个乐于助人的AI助手。你可以使用以下工具：
- 读取、写入和编辑文件
- 执行shell命令
- 搜索网页并获取网页内容
- 向聊天渠道的用户发送消息
- 生成子代理处理复杂的后台任务

## 当前时间
{now}

## 运行环境
{runtime}

## 工作区
你的工作区路径：{workspace_path}
- 记忆文件：{workspace_path}/memory/MEMORY.md
- 每日笔记：{workspace_path}/memory/YYYY-MM-DD.md
- 自定义技能：{workspace_path}/skills/{{skill-name}}/SKILL.md

重要提示：当回答直接问题或参与对话时，请直接返回文本响应。
仅当需要向特定聊天渠道（如WhatsApp）发送消息时，才使用'message'工具。
对于普通对话，只需返回文本内容 - 不要调用message工具。

始终保持乐于助人、准确且简洁的风格。使用工具时，请说明你正在执行的操作。
需要记录信息时，请写入 {workspace_path}/memory/MEMORY.md 文件"""
    
    def _load_bootstrap_files(self) -> str:
        """从工作区加载所有引导文件。"""
        parts = []
        
        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")
        
        return "\n\n".join(parts) if parts else ""
    
    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        构建用于LLM调用的完整消息列表。

        参数：
            history: 历史对话消息列表。
            current_message: 新的用户消息内容。
            skill_names: 可选的技能名称列表，用于指定要包含的技能。
            media: 可选的本地图片/媒体文件路径列表。
            channel: 当前渠道（telegram、飞书等）。
            chat_id: 当前聊天/用户ID。

        返回：
            包含系统提示词的完整消息列表。
        """
        messages = []

        # 系统提示词
        system_prompt = self.build_system_prompt(skill_names)
        if channel and chat_id:
            system_prompt += f"\n\n## 当前会话\n渠道：{channel}\n聊天ID：{chat_id}"
        messages.append({"role": "system", "content": system_prompt})

        # 历史消息
        messages.extend(history)

        # 当前消息（包含可选的图片附件）
        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})

        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """构建包含可选base64编码图片的用户消息内容。"""
        if not media:
            return text
        
        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        
        if not images:
            return text
        return images + [{"type": "text", "text": text}]
    
    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str
    ) -> list[dict[str, Any]]:
        """
        向消息列表中添加工具执行结果。
        
        参数：
            messages: 当前的消息列表。
            tool_call_id: 工具调用的ID。
            tool_name: 工具名称。
            result: 工具执行结果。
        
        返回：
            更新后的消息列表。
        """
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result
        })
        return messages
    
    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        向消息列表中添加助手消息。
        
        参数：
            messages: 当前的消息列表。
            content: 消息内容。
            tool_calls: 可选的工具调用列表。
            reasoning_content: 思考过程输出（适配Kimi、DeepSeek-R1等模型）。
        
        返回：
            更新后的消息列表。
        """
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        
        if tool_calls:
            msg["tool_calls"] = tool_calls
        
        # 支持思考过程的模型需要此字段，否则会拒绝历史记录
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        
        messages.append(msg)
        return messages
