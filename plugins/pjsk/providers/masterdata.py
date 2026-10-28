"""静态主数据源 —— Sekai World 的 master data（GitHub Pages 上的 JSON 文件）。

数据源
------
``https://sekai-world.github.io/sekai-master-db{后缀}-diff/``

====  ===============================  ============
区服  后缀                            实测大小
====  =============================  ============
日服  ``""``（无后缀）                events 2.8MB
国服  ``-cn``                         events 1.4MB
台服  ``-tc``
韩服  ``-kr``
国际  ``-en``
====  ===============================  ============

这是 **Sekai Viewer（sekai.best）自己在用的源** —— 它的前端配置里就写着
``VITE_JSON_DOMAIN_MASTER=https://sekai-world.github.io``，说明「拉 JSON + 前端过滤」
是这个生态的标准做法。

可用文件（实测）
----------------
``events.json`` 活动、``musics.json`` 歌曲、``musicDifficulties.json`` 谱面难度、
``cards.json`` 卡牌、``gachas.json`` 卡池、``gameCharacters.json`` 角色、
``eventDeckBonuses.json`` 加成、``musicVocals.json``、``characterProfiles.json``、
``versions.json`` 数据版本。

**注意**：``characters.json`` 不存在（404），角色要用 ``gameCharacters.json``。

大文件提醒
----------
日服 ``gachas.json`` **有 47.9MB**、``cards.json`` 数 MB。所以：

* 卡池/卡牌查询**必须先查缓存**（:mod:`.cache` 已保证），别每次拉
* :meth:`MasterDataProvider.get_by_id` 提供了「拉全量再本地筛」的通用写法，
  但**对超大文件不推荐**；用到时应当只取需要的字段后重新缓存
"""

from __future__ import annotations

from typing import Any

from nonebot import logger

from ..cache import fetch_json
from ..config import cfg
from ..models import Event, Music
from .base import Capability, Provider

#: 区服 -> master data 仓库后缀
_MASTER_BASES: dict[str, str] = {
    "jp": "https://sekai-world.github.io/sekai-master-db-diff",
    "cn": "https://sekai-world.github.io/sekai-master-db-cn-diff",
    "tw": "https://sekai-world.github.io/sekai-master-db-tc-diff",
    "kr": "https://sekai-world.github.io/sekai-master-db-kr-diff",
    "en": "https://sekai-world.github.io/sekai-master-db-en-diff",
}

#: 国服的备用源（Haruki 的国服 master 备份，内容与上面一致）
_CN_BACKUP = "https://raw.githubusercontent.com/Team-Haruki/haruki-sekai-sc-master/master"


class MasterDataProvider(Provider):
    """静态主数据（活动 / 歌曲 / 谱面 / 卡牌 / 卡池 / 角色）。"""

    name = "masterdata"
    desc = "Sekai World 静态主数据（GitHub Pages）"
    regions = frozenset({"cn", "jp", "tw", "kr", "en"})
    capabilities = frozenset(
        {
            Capability.EVENT,
            Capability.MUSIC,
            Capability.CHART,
            Capability.CARD,
            Capability.GACHA,
            Capability.CHARACTER,
        }
    )
    priority = 10  # 静态数据首选它

    # ------------------------------------------------------------ 取原始文件

    def base_url(self, region: str) -> str:
        """该区服的 master data 根地址。"""
        return _MASTER_BASES.get(region, _MASTER_BASES["jp"])

    async def raw_file(
        self, region: str, filename: str, *, ttl: float | None = None
    ) -> Any:
        """取一个 master data 文件（自动缓存）。

        缓存 key 里带区服，所以国服和日服的同名文件互不影响。
        """
        url = f"{self.base_url(region)}/{filename}"
        return await fetch_json(
            url,
            cache_key=f"md_{region}_{filename}",
            ttl=cfg.ttl_master if ttl is None else ttl,
        )

    # ------------------------------------------------------------ 活动

    async def get_events(self, region: str) -> list[Event]:
        """全部活动（含已结束与未开始），按 id 升序。"""
        raw = await self.raw_file(region, "events.json")
        if not isinstance(raw, list):
            logger.warning("[pjsk] {} events.json 结构异常", region)
            return []
        events = [Event.from_master(e, region) for e in raw if isinstance(e, dict)]
        events.sort(key=lambda e: e.id)
        return events

    async def get_current_event(self, region: str) -> Event | None:
        """当前正在进行的活动。"""
        for event in reversed(await self.get_events(region)):
            if event.is_running:
                return event
        return None

    async def get_upcoming_event(self, region: str) -> Event | None:
        """下一期活动（还没开始的最近一个）。

        **国服特有优势**：国服 master data 里包含尚未开始的活动，
        所以能提前看到下一期；日服的数据包里通常只有已开的。
        """
        now_candidates = [e for e in await self.get_events(region) if e.status() == "upcoming"]
        if not now_candidates:
            return None
        return min(now_candidates, key=lambda e: e.start_at or e.aggregate_at)

    async def get_recent_events(self, region: str, limit: int = 5) -> list[Event]:
        """最近几期活动（按开始时间倒序，含进行中的）。"""
        events = [e for e in await self.get_events(region) if e.start_at]
        events.sort(key=lambda e: e.start_at, reverse=True)
        return events[:limit]

    # ------------------------------------------------------------ 歌曲

    async def get_musics(self, region: str) -> list[Music]:
        """全部歌曲，按 id 升序。"""
        raw = await self.raw_file(region, "musics.json")
        if not isinstance(raw, list):
            return []
        musics = [Music.from_master(m, region) for m in raw if isinstance(m, dict)]
        musics.sort(key=lambda m: m.id)
        return musics

    # ------------------------------------------------------------ 谱面

    async def get_music_difficulties(self, region: str) -> list[dict]:
        """谱面难度列表（含等级）。"""
        raw = await self.raw_file(region, "musicDifficulties.json")
        return raw if isinstance(raw, list) else []

    # ------------------------------------------------------------ 卡牌

    async def get_cards(self, region: str) -> list[dict]:
        """卡牌列表。"""
        raw = await self.raw_file(region, "cards.json")
        return raw if isinstance(raw, list) else []

    # ------------------------------------------------------------ 卡池

    async def get_gachas(self, region: str) -> list[dict]:
        """卡池列表。"""
        raw = await self.raw_file(region, "gachas.json")
        return raw if isinstance(raw, list) else []

    # ------------------------------------------------------------ 角色

    async def get_characters(self, region: str) -> list[dict]:
        """角色列表（注意文件名是 ``gameCharacters.json``，不是 ``characters.json``）。"""
        raw = await self.raw_file(region, "gameCharacters.json")
        return raw if isinstance(raw, list) else []

    # ------------------------------------------------------------ 版本

    async def get_versions(self, region: str) -> dict:
        """数据版本（``appVersion`` / ``dataVersion``）。"""
        raw = await self.raw_file(region, "versions.json")
        return raw if isinstance(raw, dict) else {}


#: 本数据源的实例（由 registry 负责注册，避免循环导入）
provider = MasterDataProvider()