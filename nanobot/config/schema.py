"""使用Pydantic的配置模式。"""

from pathlib import Path
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class WhatsAppConfig(BaseModel):
    """WhatsApp渠道配置。"""
    enabled: bool = False
    bridge_url: str = "ws://localhost:3001"
    allow_from: list[str] = Field(default_factory=list)  # 允许的电话号码


class TelegramConfig(BaseModel):
    """Telegram渠道配置。"""
    enabled: bool = False
    token: str = ""  # 来自@BotFather的机器人令牌
    allow_from: list[str] = Field(default_factory=list)  # 允许的用户ID或用户名
    proxy: str | None = None  # HTTP/SOCKS5代理URL，例如 "http://127.0.0.1:7890" 或 "socks5://127.0.0.1:1080"


class FeishuConfig(BaseModel):
    """使用WebSocket长连接的飞书/ Lark渠道配置。"""
    enabled: bool = False
    app_id: str = ""  # 来自飞书开放平台的App ID
    app_secret: str = ""  # 来自飞书开放平台的App Secret
    encrypt_key: str = ""  # 事件订阅的加密密钥（可选）
    verification_token: str = ""  # 事件订阅的验证令牌（可选）
    allow_from: list[str] = Field(default_factory=list)  # 允许的用户open_id


class DingTalkConfig(BaseModel):
    """使用Stream模式的钉钉渠道配置。"""
    enabled: bool = False
    client_id: str = ""  # AppKey
    client_secret: str = ""  # AppSecret
    allow_from: list[str] = Field(default_factory=list)  # 允许的staff_id


class DiscordConfig(BaseModel):
    """Discord渠道配置。"""
    enabled: bool = False
    token: str = ""  # 来自Discord开发者门户的机器人令牌
    allow_from: list[str] = Field(default_factory=list)  # 允许的用户ID
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377  # GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT

class EmailConfig(BaseModel):
    """邮件渠道配置（IMAP入站 + SMTP出站）。"""
    enabled: bool = False
    consent_granted: bool = False  # 显式的所有者权限，允许访问邮箱数据

    # IMAP (接收)
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_use_ssl: bool = True

    # SMTP (发送)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    from_address: str = ""

    # 行为
    auto_reply_enabled: bool = True  # 如果为false，入站邮件会被读取但不会自动回复
    poll_interval_seconds: int = 30
    mark_seen: bool = True
    max_body_chars: int = 12000
    subject_prefix: str = "Re: "
    allow_from: list[str] = Field(default_factory=list)  # 允许的发件人邮箱地址


class ChannelsConfig(BaseModel):
    """聊天渠道配置。"""
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)


class AgentDefaults(BaseModel):
    """默认代理配置。"""
    workspace: str = "~/.nanobot/workspace"
    model: str = "anthropic/claude-opus-4-5"
    max_tokens: int = 8192
    temperature: float = 0.7
    max_tool_iterations: int = 20


class AgentsConfig(BaseModel):
    """代理配置。"""
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(BaseModel):
    """LLM提供商配置。"""
    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # 自定义头部（例如 AiHubMix 的 APP-Code）


class ProvidersConfig(BaseModel):
    """LLM提供商配置。"""
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)  # 阿里云通义千问
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)  # AiHubMix API网关
    volcengine: ProviderConfig = Field(default_factory=ProviderConfig)  # 火山方舟


class GatewayConfig(BaseModel):
    """网关/服务器配置。"""
    host: str = "0.0.0.0"
    port: int = 18790


class WebSearchConfig(BaseModel):
    """网络搜索工具配置。"""
    api_key: str = ""  # Brave Search API密钥
    max_results: int = 5


class WebToolsConfig(BaseModel):
    """网络工具配置。"""
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(BaseModel):
    """Shell执行工具配置。"""
    timeout: int = 60


class ToolsConfig(BaseModel):
    """工具配置。"""
    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    restrict_to_workspace: bool = False  # 如果为true，将所有工具访问限制在工作区目录


class Config(BaseSettings):
    """nanobot的根配置。"""
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    
    @property
    def workspace_path(self) -> Path:
        """获取展开的工作区路径。"""
        return Path(self.agents.defaults.workspace).expanduser()
    
    def _match_provider(self, model: str | None = None) -> tuple["ProviderConfig | None", str | None]:
        """匹配提供商配置及其注册表名称。返回（配置，spec_name）。"""
        from nanobot.providers.registry import PROVIDERS
        model_lower = (model or self.agents.defaults.model).lower()

        # 按关键字匹配（顺序遵循PROVIDERS注册表）
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and any(kw in model_lower for kw in spec.keywords) and p.api_key:
                return p, spec.name

        # 回退：首先是网关，然后是其他（遵循注册表顺序）
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and p.api_key:
                return p, spec.name
        return None, None

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """获取匹配的提供商配置（api_key、api_base、extra_headers）。回退到第一个可用的。"""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """获取匹配提供商的注册表名称（例如 "deepseek"、"openrouter"）。"""
        _, name = self._match_provider(model)
        return name

    def get_api_key(self, model: str | None = None) -> str | None:
        """获取给定模型的API密钥。回退到第一个可用的密钥。"""
        p = self.get_provider(model)
        return p.api_key if p else None
    
    def get_api_base(self, model: str | None = None) -> str | None:
        """获取给定模型的API基础URL。为已知网关应用默认URL。"""
        from nanobot.providers.registry import find_by_name
        p, name = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        # 只有网关在这里获得默认的api_base。标准提供商
        # （如Moonshot）通过_setup_env中的环境变量设置其基础URL
        # 以避免污染全局的litellm.api_base。
        if name:
            spec = find_by_name(name)
            if spec and spec.is_gateway and spec.default_api_base:
                return spec.default_api_base
        return None
    
    class Config:
        env_prefix = "NANOBOT_"
        env_nested_delimiter = "__"
