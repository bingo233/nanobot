"""Agent 能力的技能加载器。"""

import json
import os
import re
import shutil
from pathlib import Path

# 内置技能默认目录（相对于当前文件）
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"


class SkillsLoader:
    """
    Agent 技能加载器。
    
    技能以 markdown 文件（SKILL.md）形式存在，用于指导 agent 如何使用
    特定工具或执行特定任务。
    """
    
    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None):
        self.workspace = workspace  # 工作区路径
        self.workspace_skills = workspace / "skills"  # 工作区技能目录
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR  # 内置技能目录
    
    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        列出所有可用技能。
        
        参数:
            filter_unavailable: 若为 True，过滤掉未满足依赖要求的技能。
        
        返回:
            技能信息字典列表，包含 'name'（名称）、'path'（路径）、'source'（来源）字段。
        """
        skills = []
        
        # 工作区技能（优先级最高）
        if self.workspace_skills.exists():
            for skill_dir in self.workspace_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists():
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "workspace"})
        
        # 内置技能
        if self.builtin_skills and self.builtin_skills.exists():
            for skill_dir in self.builtin_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    # 仅添加未在工作区中存在的内置技能
                    if skill_file.exists() and not any(s["name"] == skill_dir.name for s in skills):
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "builtin"})
        
        # 根据依赖要求过滤技能
        if filter_unavailable:
            return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills
    
    def load_skill(self, name: str) -> str | None:
        """
        根据名称加载技能内容。
        
        参数:
            name: 技能名称（对应目录名）。
        
        返回:
            技能文件内容；若未找到则返回 None。
        """
        # 优先检查工作区技能
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill.read_text(encoding="utf-8")
        
        # 检查内置技能
        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill.read_text(encoding="utf-8")
        
        return None
    
    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        加载指定技能并格式化为 agent 上下文可用的内容。
        
        参数:
            skill_names: 需要加载的技能名称列表。
        
        返回:
            格式化后的技能内容字符串。
        """
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                content = self._strip_frontmatter(content)  # 移除前置元数据
                parts.append(f"### 技能: {name}\n\n{content}")
        
        return "\n\n---\n\n".join(parts) if parts else ""
    
    def build_skills_summary(self) -> str:
        """
        构建所有技能的摘要信息（名称、描述、路径、可用性）。
        
        用于渐进式加载 - agent 可在需要时通过 read_file 工具读取完整的技能内容。
        
        返回:
            XML 格式的技能摘要字符串。
        """
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""
        
        def escape_xml(s: str) -> str:
            """转义 XML 特殊字符"""
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        lines = ["<skills>"]
        for s in all_skills:
            name = escape_xml(s["name"])
            path = s["path"]
            desc = escape_xml(self._get_skill_description(s["name"]))
            skill_meta = self._get_skill_meta(s["name"])
            available = self._check_requirements(skill_meta)
            
            lines.append(f"  <skill available=\"{str(available).lower()}\">")
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{path}</location>")
            
            # 为不可用的技能显示缺失的依赖项
            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")
            
            lines.append(f"  </skill>")
        lines.append("</skills>")
        
        return "\n".join(lines)
    
    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """获取缺失的依赖项描述信息"""
        missing = []
        requires = skill_meta.get("requires", {})
        # 检查缺失的 CLI 工具
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"命令行工具: {b}")
        # 检查缺失的环境变量
        for env in requires.get("env", []):
            if not os.environ.get(env):
                missing.append(f"环境变量: {env}")
        return ", ".join(missing)
    
    def _get_skill_description(self, name: str) -> str:
        """从技能前置元数据中获取描述信息"""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name  # 回退到技能名称
    
    def _strip_frontmatter(self, content: str) -> str:
        """移除 markdown 内容中的 YAML 前置元数据"""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content
    
    def _parse_nanobot_metadata(self, raw: str) -> dict:
        """从前置元数据中解析 nanobot 相关的 JSON 元数据"""
        try:
            data = json.loads(raw)
            return data.get("nanobot", {}) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    
    def _check_requirements(self, skill_meta: dict) -> bool:
        """检查技能的依赖项是否满足（命令行工具、环境变量）"""
        requires = skill_meta.get("requires", {})
        # 检查所有必需的 CLI 工具
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        # 检查所有必需的环境变量
        for env in requires.get("env", []):
            if not os.environ.get(env):
                return False
        return True
    
    def _get_skill_meta(self, name: str) -> dict:
        """获取技能的 nanobot 元数据（缓存于前置元数据中）"""
        meta = self.get_skill_metadata(name) or {}
        return self._parse_nanobot_metadata(meta.get("metadata", ""))
    
    def get_always_skills(self) -> list[str]:
        """获取标记为 always=true 且满足依赖要求的技能列表"""
        result = []
        for s in self.list_skills(filter_unavailable=True):
            meta = self.get_skill_metadata(s["name"]) or {}
            skill_meta = self._parse_nanobot_metadata(meta.get("metadata", ""))
            # 兼容两种元数据写法
            if skill_meta.get("always") or meta.get("always"):
                result.append(s["name"])
        return result
    
    def get_skill_metadata(self, name: str) -> dict | None:
        """
        从技能的前置元数据中解析元数据字典。
        
        参数:
            name: 技能名称。
        
        返回:
            元数据字典；若未找到/解析失败则返回 None。
        """
        content = self.load_skill(name)
        if not content:
            return None
        
        if content.startswith("---"):
            match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if match:
                # 简易 YAML 解析（仅处理键值对）
                metadata = {}
                for line in match.group(1).split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        metadata[key.strip()] = value.strip().strip('"\'')
                return metadata
        
        return None