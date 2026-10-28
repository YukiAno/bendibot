"""Provider 抽象基类 —— 定义「数据源」应该长什么样。

核心思想
--------
PJSK 没有官方 API，社区数据源各管一块，而且**覆盖的区服不一样**：

* master data（GitHub 静态文件）—— 全 5 个区服都有
* ``api.sekai.best``（榜线）—— **只有 jp/en/tw/kr，没有 cn**
* 33Kit（榜线预测）—— **只有 jp**
* SnowyBot（榜线预测）—— jp/cn，但目前源站宕机

如果把这些判断写死在指令里，回答「国服榜线」时会抛一个看不懂的异常。
所以每个 Provider 都**显式声明**自己的 ``regions`` 和 ``capabilities``，
上层用 :func:`registry.find_provider` 查询；**查不到就优雅地告诉用户
「国服暂不支持榜线查询」**，而不是崩掉。
"""

from __future__ import annotations

from abc import ABC
from enum import Enum


class Capability(str, Enum):
    """数据源能提供的能力（按功能划分，不按接口划分）。"""

    #: 活动列表 / 当前活动 / 活动剩余时间
    EVENT = "event"
    #: 歌曲（曲名、作曲、分类）
    MUSIC = "music"
    #: 谱面难度与定数
    CHART = "chart"
    #: 卡牌
    CARD = "card"
    #: 卡池
    GACHA = "gacha"
    #: 角色
    CHARACTER = "character"
    #: 榜线（实时/历史排名分数）
    BORDER = "border"
    #: 榜线预测
    FORECAST = "forecast"
    #: 玩家个人信息（需抓包/登录，本插件不实现）
    PROFILE = "profile"


#: 能力的中文名（用于拼「国服暂不支持 XX」这类提示）
CAPABILITY_NAMES: dict[Capability, str] = {
    Capability.EVENT: "活动查询",
    Capability.MUSIC: "歌曲查询",
    Capability.CHART: "谱面查询",
    Capability.CARD: "卡牌查询",
    Capability.GACHA: "卡池查询",
    Capability.CHARACTER: "角色查询",
    Capability.BORDER: "榜线查询",
    Capability.FORECAST: "榜线预测",
    Capability.PROFILE: "个人信息查询",
}


class Provider(ABC):
    """数据源基类。

    子类通常只需要设置三个类属性 + 实现若干 ``async`` 取数方法。
    """

    #: 源的名字，用于日志与调试（例如 ``"masterdata"``）
    name: str = "unnamed"

    #: 人类可读的说明，用于「数据源列表」指令
    desc: str = ""

    #: 支持的区服代码集合
    regions: frozenset[str] = frozenset()

    #: 提供的能力集合
    capabilities: frozenset[Capability] = frozenset()

    #: 优先级，**数字越小越优先**。同一个能力有多个源时按它排序
    priority: int = 100

    # ------------------------------------------------------------ 查询

    def supports(self, capability: Capability, region: str) -> bool:
        """本源能否提供「某区服的某能力」。"""
        return capability in self.capabilities and region in self.regions

    def supports_any(self, region: str) -> bool:
        """本源是否支持该区服（不论能力）。"""
        return region in self.regions

    # ------------------------------------------------------------ 展示

    def region_text(self) -> str:
        """支持区服的中文描述。"""
        from ..config import region_label

        if not self.regions:
            return "—"
        order = ("cn", "jp", "tw", "kr", "en")
        ordered = [r for r in order if r in self.regions]
        ordered += [r for r in sorted(self.regions) if r not in order]
        return "/".join(region_label(r) for r in ordered)

    def capability_text(self) -> str:
        """支持能力的中文描述。"""
        order = list(Capability)
        ordered = [c for c in order if c in self.capabilities]
        return "、".join(CAPABILITY_NAMES.get(c, c.value) for c in ordered) or "—"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Provider {self.name} regions={sorted(self.regions)} caps={len(self.capabilities)}>"