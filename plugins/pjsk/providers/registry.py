"""Provider 注册表 —— 按「能力 + 区服」路由到具体数据源。

上层这样用::

    provider = find_provider(Capability.EVENT, "cn")
    if provider is None:
        return unsupported_hint(Capability.EVENT, "cn")   # 「国服暂不支持…」
    events = await provider.get_events("cn")

同一个能力有多个源时按 ``priority`` 排序（数字小的先用）。比如榜线预测
将来可以同时挂 33Kit、SekaRun、SnowyBot，日服优先用 33Kit，国服自动落到 SnowyBot。
"""

from __future__ import annotations

from .base import CAPABILITY_NAMES, Capability, Provider

#: 全部已注册的数据源（保持注册顺序）
_ALL: list[Provider] = []

#: 名字索引
_BY_NAME: dict[str, Provider] = {}

#: 区服展示顺序
_REGION_ORDER = ("cn", "jp", "tw", "kr", "en")


def register(provider: Provider) -> Provider:
    """注册一个数据源。重复注册同名源时以最后一个为准（不报错）。"""
    if provider.name in _BY_NAME:
        _ALL[:] = [p for p in _ALL if p.name != provider.name]
    _ALL.append(provider)
    _BY_NAME[provider.name] = provider
    return provider


def all_providers() -> list[Provider]:
    """全部数据源，按 priority 排序。"""
    return sorted(_ALL, key=lambda p: p.priority)


def get(name: str) -> Provider | None:
    """按名字取数据源。"""
    return _BY_NAME.get(name)


def find_providers(capability: Capability, region: str) -> list[Provider]:
    """列出能提供「某区服某能力」的所有源，按 priority 排序。"""
    hits = [p for p in _ALL if p.supports(capability, region)]
    return sorted(hits, key=lambda p: p.priority)


def find_provider(
    capability: Capability, region: str, *, exclude: tuple[str, ...] = ()
) -> Provider | None:
    """找**首选**数据源；没有则返回 None（上层据此给友好提示）。"""
    for p in find_providers(capability, region):
        if p.name not in exclude:
            return p
    return None


def supported_regions(capability: Capability) -> list[str]:
    """哪些区服能提供该能力（用于提示文案）。"""
    regions = {r for p in _ALL if capability in p.capabilities for r in p.regions}
    ordered = [r for r in _REGION_ORDER if r in regions]
    ordered += [r for r in sorted(regions) if r not in _REGION_ORDER]
    return ordered


def supported_capabilities(region: str) -> list[Capability]:
    """某区服支持哪些能力。"""
    caps = {c for p in _ALL if region in p.regions for c in p.capabilities}
    return [c for c in Capability if c in caps]


def has_capability(capability: Capability, region: str) -> bool:
    """该区服是否支持该能力。"""
    return find_provider(capability, region) is not None


def unsupported_hint(capability: Capability, region: str) -> str:
    """生成「该区服不支持该功能」的人话提示。"""
    from ..config import region_label

    cap_name = CAPABILITY_NAMES.get(capability, capability.value)
    available = supported_regions(capability)

    if not available:
        return f"{cap_name}暂时没有可用的数据源。"
    labels = "、".join(region_label(r) for r in available)
    return f"{region_label(region)}暂不支持{cap_name}，目前只有{labels}可用。"


# ---------------------------------------------------------------------------
# 注册具体数据源
#
# 做法是「注册表主动导入数据源并登记」，而不是「数据源自己 import 注册表」，
# 这样依赖方向是单向的（registry -> provider），不会出现循环导入。
#
# 新增数据源：写一个 providers/xxx.py，暴露模块级变量 `provider`，然后在下面加两行。
# ---------------------------------------------------------------------------
from . import forecast_33kit, masterdata, sekai_best  # noqa: E402

register(masterdata.provider)  # 静态数据：活动/歌曲/谱面/卡牌/卡池/角色，全 5 区服
register(sekai_best.provider)  # 实时榜线：jp/en/tw/kr（**无国服**）
register(forecast_33kit.provider)  # 榜线预测：仅日服

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