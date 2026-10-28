"""实时榜线数据源 —— ``api.sekai.best``。

这是什么
--------
Sekai Viewer（sekai.best）背后的公开榜线 API。群友说的「榜线」「分数线」
就从这里来。

.. important::
   **不支持国服。** 实测 ``?region=cn`` 返回::

       400 {"message": "region must be one of jp, tw, en, kr"}

   这是本插件唯一一个「国服做不到」的功能，属于数据源本身的限制，
   不是实现问题。查询国服榜线时会得到一句友好提示，而不是报错。

用到的接口
----------
``GET /event/live?region={region}``
    一次返回**当前活动全部档位的分数线**，实测：

    * 日服 119 条，从 ``rank=300000`` 到 ``rank=1``
    * ``1``~``100`` 名是逐名给出的；大档位是 200/300/400/500/1000/1500/
      2000/2500/3000/4000/5000/10000/20000/30000/40000/50000/100000/
      200000/300000
    * 响应约 94 KB，时间戳精确到分钟，**基本实时**

``GET /event/{id}/rankings?region={region}``
    历史榜线，默认一页 1000 条（``totalCount`` 可能 20 万+），响应约 827 KB。
    **较大，只在需要查历史时才用**，且按页取。

``GET /event/{id}/rankings/time?region={region}``
    只返回有时间戳的列表（4.6 KB），用于知道数据覆盖哪些时刻。
"""

from __future__ import annotations

from typing import Any

from ..cache import fetch_json
from ..config import cfg
from ..models import BorderLine
from .base import Capability, Provider

#: API 根地址
BASE_URL = "https://api.sekai.best"

#: 该 API 支持的区服（**没有 cn**）
SUPPORTED_REGIONS = frozenset({"jp", "en", "tw", "kr"})

#: 常用档位（查询时优先显示这些）
COMMON_RANKS: tuple[int, ...] = (
    100,
    200,
    300,
    400,
    500,
    1000,
    1500,
    2000,
    2500,
    3000,
    4000,
    5000,
    10000,
    20000,
    30000,
    50000,
    100000,
    200000,
    300000,
)


class SekaiBestProvider(Provider):
    """``api.sekai.best`` 实时榜线。"""

    name = "sekai_best"
    desc = "api.sekai.best 实时榜线（第三方公开 API）"
    regions = SUPPORTED_REGIONS
    capabilities = frozenset({Capability.BORDER})
    priority = 20

    # ------------------------------------------------------------ 当前榜线

    async def get_borders(
        self, region: str, *, force: bool = False
    ) -> list[BorderLine]:
        """当前活动全部档位的分数线。

        :param force: 忽略缓存强制重新拉取
        """
        if region not in self.regions:
            raise ValueError(f"api.sekai.best 不支持区服 {region}")

        cache_key = f"border_live_{region}"
        if force:
            from ..cache import clear_cache

            clear_cache(cache_key)

        data = await fetch_json(
            f"{BASE_URL}/event/live?region={region}",
            cache_key=cache_key,
            ttl=cfg.ttl_border,
        )
        items = (data or {}).get("data", {}).get("eventRankings") or []
        borders = [BorderLine.from_api(item, region) for item in items if isinstance(item, dict)]
        borders.sort(key=lambda b: b.rank)
        return borders

    async def get_border(self, region: str, rank: int) -> BorderLine | None:
        """某个档位的分数线。"""
        for border in await self.get_borders(region):
            if border.rank == rank:
                return border
        return None

    async def get_common_borders(
        self, region: str, ranks: tuple[int, ...] = COMMON_RANKS
    ) -> list[BorderLine]:
        """一批常用档位的分数线（保持传入顺序）。"""
        mapping = {b.rank: b for b in await self.get_borders(region)}
        return [mapping[r] for r in ranks if r in mapping]

    # ------------------------------------------------------------ 历史

    async def get_border_history(
        self, region: str, event_id: int, *, page: int = 0, limit: int = 1000
    ) -> dict[str, Any]:
        """历史榜线（**响应较大**，默认一页 1000 条）。"""
        data = await fetch_json(
            f"{BASE_URL}/event/{event_id}/rankings?region={region}"
            f"&page={page}&limit={limit}",
            cache_key=f"border_hist_{region}_{event_id}_{page}_{limit}",
            ttl=cfg.ttl_border,
        )
        return (data or {}).get("data", {}) or {}

    async def get_history_timestamps(self, region: str, event_id: int) -> list[str]:
        """历史数据覆盖的时间点列表（很小，4.6 KB）。"""
        data = await fetch_json(
            f"{BASE_URL}/event/{event_id}/rankings/time?region={region}",
            cache_key=f"border_time_{region}_{event_id}",
            ttl=cfg.ttl_border,
        )
        points = (data or {}).get("data") or []
        return [str(p) for p in points]


#: 本数据源实例（由 registry 注册）
provider = SekaiBestProvider()