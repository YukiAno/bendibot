"""榜线预测数据源 —— 33Kit。

数据源
------
``https://sekai-data.3-3.dev/predict.json``

来自 [33Kit](https://3-3.dev/pjsk-predict)。**只服务日服**（Lunabot 的配置里
``33kit.regions: ["jp"]``，实测也只有日服活动 ID 能对上）。

返回结构（实测）::

    {"status": "success", "message": "",
     "data": {"50": 98843721, "100": 83225577, "500": 27026511,
              "1000": 22473761, "5000": 4994386, "10000": 3588182,
              "50000": 1440799, ...}}

键是**字符串形式的排名档位**，值是预测的**最终分数线**（活动结束时的分数）。

.. note::
   本插件一共找了 4 个预测源（33Kit / SekaRun / SnowyBot / 本地自算），
   实测状态见 ``providers/`` 各文件顶部说明。目前**只有 33Kit 稳定可用**，
   而且只覆盖日服。国服榜线预测暂时无解（SnowyBot 源站 522 宕机，
   而国服没有公开的榜线数据可供本地推算）。
"""

from __future__ import annotations

from ..cache import fetch_json
from ..config import cfg
from .base import Capability, Provider

#: 33Kit 的预测接口
PREDICT_URL = "https://sekai-data.3-3.dev/predict.json"


class Forecast33KitProvider(Provider):
    """33Kit 榜线预测（仅日服）。"""

    name = "forecast_33kit"
    desc = "33Kit 榜线预测（仅日服）"
    regions = frozenset({"jp"})
    capabilities = frozenset({Capability.FORECAST})
    priority = 30

    async def get_forecast(self, region: str = "jp") -> dict[int, int]:
        """取预测结果：``{排名档位: 预测分数线}``。

        :raises ValueError: 区服不支持
        """
        if region not in self.regions:
            raise ValueError(f"33Kit 只支持日服，不支持 {region}")

        data = await fetch_json(
            PREDICT_URL, cache_key="forecast_33kit", ttl=cfg.ttl_border
        )
        raw = (data or {}).get("data") or {}

        result: dict[int, int] = {}
        for key, value in raw.items():
            try:
                result[int(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return result

    async def get_prediction(self, region: str, rank: int) -> int | None:
        """某个档位的预测分数线。"""
        return (await self.get_forecast(region)).get(rank)


#: 本数据源实例（由 registry 注册）
provider = Forecast33KitProvider()