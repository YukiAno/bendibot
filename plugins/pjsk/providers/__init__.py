"""数据源层。

一个数据源一个文件，每个源**自己声明**支持哪些区服、能提供哪些能力。
上层只问注册表「谁能给我日服的榜线」，不关心是谁提供的。

加一个新数据源 = 在 ``providers/`` 里加一个文件 + 在 ``registry.py`` 末尾注册一行。
"""

from __future__ import annotations

from .base import CAPABILITY_NAMES, Capability, Provider
from .registry import (
    all_providers,
    find_provider,
    find_providers,
    get,
    has_capability,
    register,
    supported_capabilities,
    supported_regions,
    unsupported_hint,
)

__all__ = [
    "CAPABILITY_NAMES",
    "Capability",
    "Provider",
    "all_providers",
    "find_provider",
    "find_providers",
    "get",
    "has_capability",
    "register",
    "supported_capabilities",
    "supported_regions",
    "unsupported_hint",
]