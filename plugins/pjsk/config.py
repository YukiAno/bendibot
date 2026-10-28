"""PJSK 插件配置 —— 全部可用 ``.env`` 覆盖，改完重启生效。

为什么自己读 .env 而不是用 NoneBot 的 ``get_plugin_config``
--------------------------------------------------------
NoneBot 的插件配置要求声明 ``Config`` 模型并注册到 driver，对「一个插件文件夹」
来说偏重；而且本项目其它插件（``llmchat_style`` / ``persona_manager``）已经统一
用 ``dotenv_values`` 直读 ``.env``。保持一致，维护起来省心。

读取顺序：**进程环境变量 > .env 文件 > 代码默认值**。

可配置项（.env 里加这些键即可）
-------------------------------
======================  ========  ==========================================
键                      默认      说明
======================  ========  ==========================================
PJSK_ENABLED            true      总开关，false 时整个插件不注册指令
PJSK_DEFAULT_REGION     cn        默认区服（不带区服参数时用哪个）
PJSK_CACHE_DIR          data/pjsk 缓存目录（相对机器人根目录）
PJSK_HTTP_TIMEOUT       20        单次 HTTP 请求超时（秒）
PJSK_TTL_EVENT          3600      活动数据缓存（秒）
PJSK_TTL_BORDER         600       榜线数据缓存（秒）
PJSK_TTL_MASTER         86400     静态数据（歌曲/卡牌）缓存（秒）
PJSK_MAX_RESULT         10        列表类指令默认显示条数
PJSK_CARD_LIST_LIMIT    40        「查卡」按角色列卡时最多显示几张
PJSK_TOOLS_ENABLED      true      是否向 AI 暴露工具（需 llmchat）
======================  ========  ==========================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

# plugins/pjsk/config.py -> plugins/pjsk -> plugins -> 项目根
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_ENV_PATH = _BASE_DIR / ".env"

# ---------------------------------------------------------------- 区服定义

#: 支持的全部区服（顺序即列表显示顺序）
REGIONS: tuple[str, ...] = ("cn", "jp", "tw", "kr", "en")

#: 区服中文名
REGION_NAMES: dict[str, str] = {
    "cn": "国服",
    "jp": "日服",
    "tw": "台服",
    "kr": "韩服",
    "en": "国际服",
}

#: 用户可能输入的各种写法 -> 标准区服代码
REGION_ALIASES: dict[str, str] = {
    "cn": "cn",
    "国服": "cn",
    "中服": "cn",
    "jp": "jp",
    "日服": "jp",
    "日": "jp",
    "tw": "tw",
    "台服": "tw",
    "繁中": "tw",
    "台湾": "tw",
    "kr": "kr",
    "韩服": "kr",
    "韩国": "kr",
    "en": "en",
    "国际服": "en",
    "美服": "en",
    "全球服": "en",
}


def resolve_region(text: str | None) -> str | None:
    """把用户输入解析成标准区服代码；无法识别时返回 None。"""
    if not text:
        return None
    return REGION_ALIASES.get(text.strip().lower()) or REGION_ALIASES.get(text.strip())


def region_label(region: str) -> str:
    """区服代码 -> 中文名（未知时原样返回）。"""
    return REGION_NAMES.get(region, region)


# ---------------------------------------------------------------- 读 .env


def _env(key: str, default: str) -> str:
    """进程环境变量优先，其次 .env，最后默认值。"""
    value = os.environ.get(key)
    if value:
        return value
    try:
        value = dotenv_values(_ENV_PATH).get(key)
    except Exception:  # .env 缺失或格式错误都不该让插件挂掉
        value = None
    return value if value else default


def _env_bool(key: str, default: bool) -> bool:
    return _env(key, "true" if default else "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)).strip())
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 配置对象


@dataclass
class PjskConfig:
    """PJSK 插件运行时配置（进程内单例 ``cfg``）。"""

    enabled: bool
    default_region: str
    cache_dir: Path
    http_timeout: float
    ttl_event: int
    ttl_border: int
    ttl_master: int
    max_result: int
    card_list_limit: int
    tools_enabled: bool

    def ensure_dirs(self) -> None:
        """确保缓存目录存在。"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)


def _load() -> PjskConfig:
    region = resolve_region(_env("PJSK_DEFAULT_REGION", "cn")) or "cn"
    cache_raw = _env("PJSK_CACHE_DIR", "data/pjsk")
    cache_dir = Path(cache_raw)
    if not cache_dir.is_absolute():
        cache_dir = _BASE_DIR / cache_dir

    return PjskConfig(
        enabled=_env_bool("PJSK_ENABLED", True),
        default_region=region,
        cache_dir=cache_dir,
        http_timeout=_env_float("PJSK_HTTP_TIMEOUT", 20.0),
        ttl_event=_env_int("PJSK_TTL_EVENT", 3600),
        ttl_border=_env_int("PJSK_TTL_BORDER", 600),
        ttl_master=_env_int("PJSK_TTL_MASTER", 86400),
        max_result=_env_int("PJSK_MAX_RESULT", 10),
        card_list_limit=_env_int("PJSK_CARD_LIST_LIMIT", 40),
        tools_enabled=_env_bool("PJSK_TOOLS_ENABLED", True),
    )


#: 全局配置单例
cfg = _load()