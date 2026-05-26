"""配置管理模块。

两阶段加载：
1. python-dotenv 将 .env 注入到 os.environ（确保 get_api_key 能读到）
2. pydantic-settings 从 .env 加载敏感字段
3. tomllib 从 config.toml 加载非敏感字段
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from utils.exceptions import ConfigError

# 将 .env 注入到 os.environ，确保 get_api_key() 能读到任意前缀的 API Key 变量
load_dotenv(dotenv_path=Path(".env"), override=False)
load_dotenv(dotenv_path=Path("configs/.env"), override=False)

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore


class TableConfig(BaseModel):
    """单个 AI 表格配置，对应 [[dingtalk.tables]]。"""

    key: str
    base_id: str
    sheet_id: str
    image_api_key_env: str  # 必填，指定使用的 AI 图片 API Key 环境变量名
    prompt_field: str = "提示词"
    model_field: str = "生图模型"
    reference_image_field: str = "素材图"
    result_image_field: str = "生成图片"
    result_status_field: str = "生成结果"
    result_time_field: str = "生成时间"


class ModelConfig(BaseModel):
    """AI 模型配置，对应 [ai.model."xxx"]。"""

    base_url: str
    model_name: str
    provider: str  # "google" 或 "openai"


class ServerConfig(BaseModel):
    """服务配置，对应 [server]。"""

    host: str = "0.0.0.0"
    port: int = 8030
    log_level: str = "INFO"
    max_concurrency: int = 5


class RetryConfig(BaseModel):
    """重试配置，对应 [ai.retry]。"""

    initial_delay: int = 3
    max_retries: int = 2


class AiConfig(BaseModel):
    """AI 中转配置，对应 [ai]。"""

    default_model: str = "Nano Banana 2"
    retry: RetryConfig = RetryConfig()
    models: dict[str, ModelConfig] = Field(default_factory=dict)


class DingtalkConfig(BaseModel):
    """钉钉表格配置，对应 [dingtalk]。"""

    default_table: str = "clothing"
    tables: list[TableConfig] = []


class _EnvSettings(BaseSettings):
    """敏感配置，仅从 .env 加载。"""

    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    dingtalk_operator_id: str = ""
    api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


class Settings(BaseSettings):
    """全局配置。

    加载顺序：
    1. python-dotenv 将 .env 注入 os.environ
    2. pydantic-settings 从 .env / 系统环境变量加载敏感字段
    3. tomllib 加载 config.toml 非敏感字段
    4. 系统环境变量覆盖 config.toml 同名字段（最高优先级）
    """

    # 敏感字段（仅 .env / 系统环境变量）
    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    dingtalk_operator_id: str = ""
    api_key: str = ""

    # 非敏感字段（由 __init__ 从 config.toml 填充）
    server: ServerConfig = ServerConfig()
    ai: AiConfig = AiConfig()
    dingtalk: DingtalkConfig = DingtalkConfig()

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def __init__(self, **kwargs) -> None:
        explicit = dict(kwargs)
        super().__init__(**explicit)
        self._load_toml_config(explicit)

    def _load_toml_config(self, explicit: dict) -> None:
        """使用 tomllib 加载 config.toml，除非键已被显式参数覆盖。"""
        toml_path = os.environ.get("CONFIG_PATH", "configs/config.toml")
        toml_file = Path(toml_path)
        if not toml_file.exists():
            return

        with toml_file.open("rb") as f:
            data = tomllib.load(f)

        if "server" in data and "server" not in explicit:
            merged = self.server.model_dump() | data["server"]
            self.server = ServerConfig(**merged)
        if "dingtalk" in data and "dingtalk" not in explicit:
            merged = self.dingtalk.model_dump() | data["dingtalk"]
            self.dingtalk = DingtalkConfig(**merged)
        if "ai" in data and "ai" not in explicit:
            ai_data = data["ai"]
            if "model" in ai_data:
                ai_data["models"] = {
                    k: ModelConfig(**v) for k, v in ai_data.pop("model", {}).items()
                }
            merged = self.ai.model_dump() | ai_data
            self.ai = AiConfig(**merged)

        self._apply_env_overrides()

    def _apply_env_overrides(self) -> None:
        """系统环境变量覆盖非敏感配置。"""
        overrides = {
            "SERVER_HOST": ("server", "host"),
            "SERVER_PORT": ("server", "port"),
            "LOG_LEVEL": ("server", "log_level"),
            "MAX_CONCURRENCY": ("server", "max_concurrency"),
            "AI_DEFAULT_MODEL": ("ai", "default_model"),
            "AI_RETRY_INITIAL_DELAY": ("ai", "retry", "initial_delay"),
            "AI_RETRY_MAX_RETRIES": ("ai", "retry", "max_retries"),
            "DINGTALK_DEFAULT_TABLE": ("dingtalk", "default_table"),
        }
        for env_key, path in overrides.items():
            if env_key not in os.environ:
                continue
            raw = os.environ[env_key]
            obj = getattr(self, path[0])
            for attr in path[1:-1]:
                obj = getattr(obj, attr)
            current = getattr(obj, path[-1])
            if isinstance(current, bool):
                typed = raw.lower() in ("true", "1", "yes")
            elif isinstance(current, int):
                typed = int(raw)
            else:
                typed = type(current)(raw)
            setattr(obj, path[-1], typed)

    def get_table(self, table_key: str | None = None) -> TableConfig:
        """根据 table_key 获取表格配置。"""
        key = table_key or self.dingtalk.default_table
        for table in self.dingtalk.tables:
            if table.key == key:
                return table
        raise ConfigError(f"Table config not found: {key}")

    def get_model(self, model_key: str) -> ModelConfig:
        """根据模型名称获取模型配置。"""
        model = self.ai.models.get(model_key)
        if not model:
            raise ConfigError(f"Model config not found: {model_key}")
        return model

    def get_api_key(self, env_var_name: str) -> str:
        """根据环境变量名获取 API Key，找不到则抛异常。

        直接读取 os.environ，支持任意前缀的环境变量名
        （如 ZHUOZHI_*, AHMI_*, HUAPU_* 等），只需在 .env 中定义即可。
        """
        value = os.environ.get(env_var_name, "")
        if not value:
            raise ConfigError(f"API key not found for env var: {env_var_name}")
        return value
