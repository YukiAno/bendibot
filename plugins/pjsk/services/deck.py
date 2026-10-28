"""模拟组卡 —— 基于公开数据的**理论最优卡组**推荐。

动机
----
群里最常见的问题是「**这期活动我该上哪套卡**」。
要回答它，正规做法是让玩家用 Haruki 工具箱抓自己的账号数据
（那是 B 档功能），但**其实不必要**：

在不知道对方有什么卡的情况下，给出**「假设全卡满破满级」的理论最优卡组**
本身就是有用的答案 —— 它告诉你「这套卡是天花板，你有的话就上这套」。

为什么叫做"模拟"
----------------
``allium-deck`` 引擎**强制要求** ``user_data``（实测报错：
``Either user_data / user_data_file_path / user_data_str is required.``）。

这一点有**源码级确证**（`empty-sekai/allium-deck` 的 Rust 源码）：

* 候选池只从 ``user.user_cards`` 构建，空池直接 ``BuildError::EmptyPool``
* ``card_config.rs::apply_card_config`` **只修改已存在的卡**
  （level / skill / master_rank / episode / canvas），
  ``disable`` 才剔除 —— **全文没有任何"按稀有度新增卡"的分支**

所以 ``rarity_N_config`` **不能**凭空给卡，只能调整"已拥有"卡的状态。

于是我们**自己造一份账号**：把 masterdata 里全部卡都写成
``level=60 masterRank=5 skillLevel=4``，区域道具全部满级，角色等级满。
这份 JSON 只有 **0.24 MB**（日服 1466 张卡），加载瞬间完成。

.. note::
   这是**模拟**账号，不是任何真实玩家的数据。
   它给出的是「理论天花板」，**不是你实际能组的卡组**。

区服支持（**五个区服都行**）
--------------------------
一开始以为只有日服能做，因为组卡要用的 ``music_metas.json``
只有日服有：:

    GET .../sekai-best-assets/music_metas.json   → 200（日服）
    GET .../sekai-cn-assets/music_metas.json     → 404（国服没有）

但后来读到引擎源码里的说明：

    Region tag carried through to the caller; **does not change engine math**

也就是说 **``region`` 只是一个标签**。而 ``music_metas`` 里存的是
**游戏机制系数**（技能槽权重、音符数、fever 系数）——
这些**本来就不分区服**：国服只是进度落后，同一首歌的谱面系数是一样的。

**所以：加载「本区服的 masterdata」+ 用「日服的 music_metas」= 正确结果。**
（✅ 实测：国服组卡成功，活动加成也算得出来，卡组里全是国服持有的卡）

各区服的 masterdata 基址不同（注意台服是 ``-tc`` 不是 ``-tw``）：
``sekai-master-db{-cn,-tc,-kr,-en}-diff``。

依赖
----
::

    pip install allium-sekai-deck

它是 **Rust 预编译的 abi3 wheel、零依赖、支持 Windows x86_64**，
不用装 Rust 或编译器。

性能
----
实测 **16~36 ms** 出结果（16 核）。
默认单线程（``DECK_ENGINE_THREADS`` 未设时为 1），**必须显式设置**。

首次使用某个区服要下载该区服 masterdata（日服 43 MB / 国服约 10 MB），
之后不再下载。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nonebot import logger

from ..config import REGIONS, cfg
from ..models import DIFFICULTY_NAMES, Music

#: 各区服的 masterdata 基址。
#: ⚠️ 台服是 **-tc** 不是 -tw（实测），国服是 -cn，其余是 -en/-kr。
MASTERDATA_BASES: dict[str, str] = {
    "jp": "https://sekai-world.github.io/sekai-master-db-diff",
    "en": "https://sekai-world.github.io/sekai-master-db-en-diff",
    "tw": "https://sekai-world.github.io/sekai-master-db-tc-diff",
    "kr": "https://sekai-world.github.io/sekai-master-db-kr-diff",
    "cn": "https://sekai-world.github.io/sekai-master-db-cn-diff",
}

#: 谱面元数据。**只有日服这一个 URL**，但它对**所有区服通用** ——
#: 里面是游戏机制系数，不含区服信息（见模块 docstring）。
MUSIC_METAS_URL = "https://storage.sekai.best/sekai-best-assets/music_metas.json"

#: 各引擎需要的 masterdata 表。
#:
#: 这份清单是**两条路互相印证**得到的：
#:
#: 1. **实测**：引擎报错会逐个说缺哪张表
#:    （``ValueError: 缺少 masterdata 表 xxx.json``），
#:    写个循环自动补齐，得到一份清单
#: 2. **源码**：``allium-deck`` 的 Rust 源码里
#:    ``MasterdataSources::from_dir`` 按字面文件名取表
#:
#: 其中前 14 个是**必需**的，后面几个是**可选**的
#: （缺失时引擎走内建兜底或空表，我们照样下载以免结果偏差）。
#:
#: 注意它**不需要** ``musics.json`` / ``musicDifficulties.json`` /
#: ``gameCharacters.json`` —— 组卡只要卡的数值、技能和活动加成，
#: **谱面信息全在 music_metas 里**。
MASTERDATA_FILES: tuple[str, ...] = (
    # ── 必需（缺失即报错） ──
    "cards.json",
    "cardRarities.json",
    "cardEpisodes.json",
    "masterLessons.json",
    "skills.json",
    "areaItemLevels.json",
    "characterRanks.json",
    "cardMysekaiCanvasBonuses.json",
    "gameCharacterUnits.json",
    "events.json",
    "eventCards.json",
    "eventDeckBonuses.json",
    "eventRarityBonusRates.json",
    "worldBloomDifferentAttributeBonuses.json",
    # ── 可选（缺失时引擎走内建兜底） ──
    "eventCardBonusLimits.json",
    "eventHonorBonuses.json",
    "eventSkillScoreUpLimits.json",
)

#: 可选表：取不到只记 debug 日志，不报错（引擎有内建兜底表）。
#: 其余都是**必需**表，取不到必须让用户看到原因。
OPTIONAL_MASTERDATA_FILES: frozenset[str] = frozenset({
    "eventCardBonusLimits.json",
    "eventHonorBonuses.json",
    "eventSkillScoreUpLimits.json",
})

#: 卡牌满级（4 星特训后）
MAX_CARD_LEVEL = 60
#: 满突破
MAX_MASTER_RANK = 5
#: 满技能
MAX_SKILL_LEVEL = 4
#: 角色等级（模拟值，取一个足够高的数）
MAX_CHARACTER_RANK = 100

#: 一次最多返回几副卡组
MAX_DECKS = 5

#: 单次搜索的超时（毫秒）
SEARCH_TIMEOUT_MS = 15000

#: 环境变量：强制引擎线程数。不设则用 CPU 核数。
THREADS_ENV = "DECK_ENGINE_THREADS"

#: ``target`` 的合法值与中文说明。
#:
#: 出处：Rust ``parse_target_checked``（engine.rs 815-827）。
#: ``live_type=mysekai`` 时 ``score`` 会被归一为 ``mysekai``。
TARGETS: dict[str, str] = {
    "score": "打歌得分（默认）",
    "power": "综合力",
    "skill": "技能效果",
    "bonus": "活动加成（**需要活动上下文**）",
    "mysekai": "MySekai 活动点数",
}


class DeckError(RuntimeError):
    """组卡失败（消息可直接给用户看）。"""


# ────────────────────────── 结果模型 ──────────────────────────


@dataclass
class DeckCard:
    """卡组里的一张卡。"""

    card_id: int
    total_power: int = 0
    master_rank: int = 0
    level: int = 0
    skill_level: int = 0
    after_training: bool = False


@dataclass
class Deck:
    """一副推荐卡组。

    .. important::
       **``score`` 才是引擎的排序依据**，``live_score`` 只是打歌得分。

       两者会出现「**带活动加成时打歌得分反而更低**」的现象，这**不是 bug**：
       带加成时引擎会优先挑**活动加成高**的卡，即使它的综合力略低 ——
       因为活动的目标是**活动点数**，不是打歌得分。
    """

    rank: int
    #: 引擎的排序依据（活动模式下已含活动加成）
    score: float
    #: 打歌预测得分
    live_score: int
    total_power: int
    base_power: int = 0
    character_bonus_power: int = 0
    area_item_bonus_power: int = 0
    #: 称号加成
    honor_bonus_power: int = 0
    #: 家具加成
    fixture_bonus_power: int = 0
    #: 门加成（MySekai）
    gate_bonus_power: int = 0
    event_bonus_rate: float = 0.0
    #: 支援编成加成率
    support_deck_bonus_rate: float = 0.0
    cards: list[DeckCard] = field(default_factory=list)

    @property
    def card_ids(self) -> list[int]:
        return [c.card_id for c in self.cards]


@dataclass
class DeckRecommendResult:
    """一次组卡的全部结果。"""

    decks: list[Deck]
    cost_ms: float
    region: str = "jp"
    music_id: int = 0
    difficulty: str = ""
    event_id: int | None = None
    target: str = "score"
    note: str = ""


# ────────────────────────── 路径 ──────────────────────────


def masterdata_dir(region: str) -> Path:
    """某区服的 masterdata 缓存目录。"""
    path = Path(cfg.cache_dir) / f"masterdata_{region}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def masterdata_ready(region: str) -> bool:
    """某区服的 masterdata 是否已经齐了。

    用 ``_looks_valid`` 排除「空数组占位」和「截断」——
    早期版本下载失败会写 ``[]``，那些文件看着存在其实没用。
    """
    target = masterdata_dir(region)
    if not _looks_valid(target / "music_metas.json"):
        return False
    return all(_looks_valid(target / f) for f in MASTERDATA_FILES)


# ────────────────────────── 下载 ──────────────────────────


async def _download_masterdata(region: str, force: bool = False) -> None:
    """下载某区服的 masterdata。

    ⚠️ **不用 ``cache.fetch_json``**，而是直接下载：
    masterdata 有自己的目录结构（一个区服一个目录），
    再经一层通用缓存会**重复存一份**（日服 cards.json 35 MB 会变 70 MB）。

    ⚠️ **必需表下载失败会直接报错**，不会写空表掩盖。
    早期的版本对所有失败都静默写 ``[]``，结果 tw/kr/en 的
    ``cards.json`` 变成 2 字节空数组，组卡时报「候选卡池为空」，
    完全看不出真正原因。**必需表失败必须让用户看到原因。**

    :raises DeckError: 必需表取不到
    """
    base = MASTERDATA_BASES.get(region)
    if not base:
        raise DeckError(f"组卡不支持「{region}」区服。")

    from ..cache import get_client

    target = masterdata_dir(region)
    client = get_client()

    for name in MASTERDATA_FILES:
        path = target / name
        if path.exists() and not force and _looks_valid(path):
            continue

        url = f"{base}/{name}"
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            # 可选的表允许缺失（引擎有内建兜底），必需的不行
            if name in OPTIONAL_MASTERDATA_FILES:
                logger.debug("[pjsk] 可选表 {}/{} 取不到，跳过：{}", region, name, exc)
                path.write_text("[]", encoding="utf-8")
                continue
            raise DeckError(
                f"下载组卡数据失败（{region}/{name}）：{exc}\n"
                "组卡第一次使用需要下载数据，请检查网络后重试。"
            ) from exc

        path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")

    # music_metas：只有日服有，但**对所有区服通用**（游戏机制系数不含区服）
    metas = target / "music_metas.json"
    if force or not _looks_valid(metas):
        jp_metas = masterdata_dir("jp") / "music_metas.json"
        if _looks_valid(jp_metas):
            metas.write_bytes(jp_metas.read_bytes())
        else:
            try:
                resp = await client.get(MUSIC_METAS_URL)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                raise DeckError(f"下载谱面元数据失败：{exc}") from exc
            metas.write_text(
                json.dumps(data, separators=(",", ":")), encoding="utf-8"
            )


def _looks_valid(path: Path) -> bool:
    """快速判断已下载的 masterdata 文件是否可用。

    **不解析整个文件**（日服 cards.json 有 35 MB），只做两项检查：

    1. 至少 2 字节
    2. 首字节是 ``[`` 或 ``{``

    .. note::
       阈值是 **2 字节**，不是更大的数 —— 因为有些表**合法地就是空数组**：
       国服的 ``eventSkillScoreUpLimits.json`` 实测就是 ``[]``（2 字节）。
       早先设成 16 字节会把这种正常文件误判为坏掉，
       导致每次组卡都白白重新下载。

       ``[]`` 和「下载失败写的 ``[]``」确实无法区分，
       但下载逻辑已经保证**必需表失败会直接报错**，不会再制造假空表。
    """
    try:
        if path.stat().st_size < 2:
            return False
        with path.open("rb") as fh:
            head = fh.read(1)
        return head in (b"[", b"{")
    except OSError:
        return False


# ────────────────────────── 引擎（每区服一个） ──────────────────────────


class _EngineHolder:
    """某个区服的引擎实例（惰性加载）。

    masterdata 有几十 MB，**不在 bot 启动时加载** ——
    只有第一次真的有人要该区服组卡时才加载。

    ⚠️ 每个区服必须**独立一个 Engine**：``update_masterdata(dir, region)``
    是把数据登记到该 region 名下，一个引擎不能同时持有两个区服。
    """

    def __init__(self, region: str) -> None:
        self.region = region
        self._engine: Any = None
        self._lock = asyncio.Lock()
        self._user_data: str | None = None

    @property
    def loaded(self) -> bool:
        return self._engine is not None

    async def get(self):
        """拿到可用的引擎（首次调用会下载并加载 masterdata）。"""
        async with self._lock:
            if self._engine is not None:
                return self._engine

            try:
                from allium_deck import Engine
                from sekai_deck_recommend_cpp import set_engine_thread_count
            except ImportError as exc:
                raise DeckError(
                    "组卡功能缺少依赖，请先安装：pip install allium-sekai-deck"
                ) from exc

            if not masterdata_ready(self.region):
                await _download_masterdata(self.region)

            # 默认单线程，必须显式设成多核（实测快很多）
            if not os.environ.get(THREADS_ENV):
                set_engine_thread_count(os.cpu_count() or 4)

            target = masterdata_dir(self.region)
            engine = Engine()
            try:
                engine.update_masterdata(str(target), self.region)
                engine.update_musicmetas(
                    str(target / "music_metas.json"), self.region
                )
            except Exception as exc:  # noqa: BLE001
                raise DeckError(f"加载组卡数据失败：{exc}") from exc

            self._engine = engine
            return engine

    def sim_account(self) -> str:
        """「全卡满破满级」的模拟账号 JSON（带缓存）。

        用**本区服**的 ``cards.json`` 造，所以不会出现该区服没有的卡。
        """
        if self._user_data is not None:
            return self._user_data

        target = masterdata_dir(self.region)
        cards = json.loads((target / "cards.json").read_text(encoding="utf-8"))

        user_cards = [
            {
                "cardId": c["id"],
                "level": MAX_CARD_LEVEL,
                "masterRank": MAX_MASTER_RANK,
                "skillLevel": MAX_SKILL_LEVEL,
                "episode1Read": True,
                "episode2Read": True,
                "afterTraining": True,
                "defaultImage": "special_training",
                "duplicateCount": 0,
            }
            for c in cards
        ]

        # 区域道具满级
        best: dict[int, int] = {}
        levels_path = target / "areaItemLevels.json"
        if levels_path.exists():
            levels = json.loads(levels_path.read_text(encoding="utf-8"))
            for row in levels:
                aid, lv = row.get("areaItemId"), row.get("level")
                if aid is not None and lv is not None:
                    best[aid] = max(best.get(aid, 0), lv)

        # 角色等级满
        char_ids: list[int] = []
        units_path = target / "gameCharacterUnits.json"
        if units_path.exists():
            units = json.loads(units_path.read_text(encoding="utf-8"))
            char_ids = sorted(
                {u["gameCharacterId"] for u in units if "gameCharacterId" in u}
            )

        payload = {
            "userCards": user_cards,
            "userAreaItems": [
                {"areaItemId": aid, "level": lv} for aid, lv in sorted(best.items())
            ],
            "userCharacters": [
                {"characterId": cid, "characterRank": MAX_CHARACTER_RANK}
                for cid in char_ids
            ],
        }
        self._user_data = json.dumps(payload, separators=(",", ":"))
        return self._user_data


_holders: dict[str, _EngineHolder] = {}


def holder(region: str) -> _EngineHolder:
    """拿到某区服的引擎持有者（不存在就建一个）。"""
    if region not in REGIONS:
        raise DeckError(f"组卡不支持「{region}」区服。")
    if region not in _holders:
        _holders[region] = _EngineHolder(region)
    return _holders[region]


# ────────────────────────── 组卡 ──────────────────────────


def _build_options(*, region: str, music_id: int, difficulty: str, limit: int,
                   event_id: int | None, target: str):
    from allium_deck import RecommendOptions

    options = RecommendOptions()
    options.region = region
    options.live_type = "solo"
    options.target = target
    options.music_id = music_id
    options.music_diff = difficulty
    options.limit = max(1, min(limit, MAX_DECKS))
    options.timeout_ms = SEARCH_TIMEOUT_MS
    options.user_data_str = holder(region).sim_account()
    if event_id:
        options.event_id = event_id
    return options


def _recommend_blocking(*, region: str, music_id: int, difficulty: str, limit: int,
                        event_id: int | None, target: str) -> DeckRecommendResult:
    """真正调用引擎（阻塞，由调用方放进线程池）。

    ⚠️ 引擎是 Rust 实现的**同步**调用，必须用 ``asyncio.to_thread`` 包装，
    否则会卡住整个事件循环 —— llmchat 那边就完全没响应了。
    """
    engine = holder(region)._engine
    options = _build_options(
        region=region, music_id=music_id, difficulty=difficulty,
        limit=limit, event_id=event_id, target=target,
    )

    try:
        raw = engine.recommend(options)
    except Exception as exc:  # noqa: BLE001
        raise DeckError(f"组卡失败：{exc}") from exc

    decks: list[Deck] = []
    for i, d in enumerate(raw.decks, start=1):
        info = d.to_dict()
        cards = [
            DeckCard(
                card_id=c.get("card_id", 0),
                total_power=c.get("total_power", 0) or 0,
                master_rank=c.get("master_rank", 0) or 0,
                level=c.get("level", 0) or 0,
                skill_level=c.get("skill_level", 0) or 0,
                after_training=bool(c.get("after_training")),
            )
            for c in (info.get("cards") or [])
        ]
        decks.append(
            Deck(
                rank=i,
                score=float(info.get("score") or 0),
                live_score=int(info.get("live_score") or 0),
                total_power=int(info.get("total_power") or 0),
                base_power=int(info.get("base_power") or 0),
                character_bonus_power=int(info.get("character_bonus_power") or 0),
                area_item_bonus_power=int(info.get("area_item_bonus_power") or 0),
                honor_bonus_power=int(info.get("honor_bonus_power") or 0),
                fixture_bonus_power=int(info.get("fixture_bonus_power") or 0),
                gate_bonus_power=int(info.get("gate_bonus_power") or 0),
                event_bonus_rate=float(info.get("event_bonus_rate") or 0),
                support_deck_bonus_rate=float(info.get("support_deck_bonus_rate") or 0),
                cards=cards,
            )
        )

    return DeckRecommendResult(
        decks=decks,
        cost_ms=float(getattr(raw, "cost_ms", 0) or 0),
        region=region,
        music_id=music_id,
        difficulty=difficulty,
        event_id=event_id,
        target=target,
    )


async def recommend(
    *,
    music_id: int,
    region: str = "cn",
    difficulty: str = "master",
    limit: int = 3,
    event_id: int | None = None,
    target: str = "score",
) -> DeckRecommendResult:
    """推荐理论最优卡组。

    :param music_id: 曲目 ID
    :param region: 区服（cn/jp/tw/kr/en）
    :param difficulty: 难度（easy/normal/hard/expert/master/append）
    :param limit: 返回几副（1~5）
    :param event_id: 活动 ID，给了就按活动加成算
    :param target: 优化目标（score/power/skill/bonus/mysekai）
    :raises DeckError: 失败（消息可直接给用户看）
    """
    if region not in REGIONS:
        raise DeckError(f"组卡不支持「{region}」区服。")

    difficulty = difficulty.lower()
    if difficulty not in DIFFICULTY_NAMES:
        raise DeckError(
            f"没有「{difficulty}」这个难度。可选："
            + "、".join(DIFFICULTY_NAMES.keys())
        )

    target = target.lower()
    if target not in TARGETS:
        raise DeckError(
            f"没有「{target}」这个优化目标。可选：" + "、".join(TARGETS.keys())
        )
    if target == "bonus" and not event_id:
        raise DeckError("按活动加成组卡需要指定活动，试试：组卡 <歌名> 活动<ID>")

    await holder(region).get()
    return await asyncio.to_thread(
        _recommend_blocking,
        region=region,
        music_id=music_id,
        difficulty=difficulty,
        limit=limit,
        event_id=event_id,
        target=target,
    )


# ────────────────────────── 卡名与活动 ──────────────────────────

_card_names: dict[str, dict[int, str]] = {}


def load_card_names(region: str) -> dict[int, str]:
    """卡 ID → 卡名（按区服缓存）。

    ``cards.json`` 日服 35 MB / 国服 4.5 MB，解析要一两秒，
    所以**只解析一次**然后缓存。

    卡名用的是 ``prefix`` 字段（masterdata 里没有别的名字字段）。
    """
    if region in _card_names:
        return _card_names[region]

    path = masterdata_dir(region) / "cards.json"
    if not path.exists():
        return {}

    cards = json.loads(path.read_text(encoding="utf-8"))
    names = {c["id"]: c.get("prefix") or f"#{c['id']}" for c in cards}
    _card_names[region] = names
    return names


def current_event_id(region: str) -> int | None:
    """某区服**当前进行中**的活动 ID（用于默认套用活动加成）。

    没有正在进行（或最近刚结束）的活动时返回 ``None``。
    """
    path = masterdata_dir(region) / "events.json"
    if not path.exists():
        return None

    events = json.loads(path.read_text(encoding="utf-8"))
    now_ms = int(time.time() * 1000)

    running = [
        e for e in events
        if (e.get("startAt") or 0) <= now_ms <= (e.get("aggregateAt") or 0)
    ]
    if running:
        return max(running, key=lambda e: e.get("startAt") or 0)["id"]

    # 没在进行中的就用最近结束的一场（刚打完还在配队也很常见）
    finished = [e for e in events if (e.get("aggregateAt") or 0) < now_ms]
    if finished:
        return max(finished, key=lambda e: e.get("aggregateAt") or 0)["id"]
    return None


# ────────────────────────── 渲染 ──────────────────────────


def _fmt_num(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def render_decks(
    result: DeckRecommendResult,
    *,
    music: Music | None = None,
    card_names: dict[int, str] | None = None,
    region_label: str = "",
    show_cards: bool = True,
) -> str:
    """把组卡结果渲染成群聊文本。"""
    lines: list[str] = []

    title = "理论最优卡组"
    if music is not None:
        title += f" · {music.title}"
    diff_label = DIFFICULTY_NAMES.get(result.difficulty, result.difficulty.upper())
    lines.append(f"【{title}】{diff_label}")

    meta: list[str] = []
    if region_label:
        meta.append(region_label)
    if result.target != "score":
        meta.append(TARGETS.get(result.target, result.target))
    if result.event_id:
        meta.append(f"活动 {result.event_id} 加成")
    if meta:
        lines.append(f"（{'、'.join(meta)}）")
    lines.append("")

    if not result.decks:
        lines.append("没有找到合适的卡组。")
        return "\n".join(lines)

    for deck in result.decks:
        head = f"第{deck.rank}名"
        if result.decks[0].score == deck.score:
            head += "（并列）"
        lines.append(f"◆ {head}")

        # ★有活动/非默认目标时，score 才是排序依据，优先显示它★
        if result.event_id or result.target != "score":
            lines.append(f"  综合评分：{_fmt_num(deck.score)}（排序依据，越高越好）")
            lines.append(f"  打歌得分：{_fmt_num(deck.live_score)}")
        else:
            lines.append(f"  预测得分：{_fmt_num(deck.live_score)}")

        lines.append(f"  综合力：{_fmt_num(deck.total_power)}")
        extras = []
        if deck.character_bonus_power:
            extras.append(f"角色 {_fmt_num(deck.character_bonus_power)}")
        if deck.honor_bonus_power:
            extras.append(f"称号 {_fmt_num(deck.honor_bonus_power)}")
        if deck.fixture_bonus_power:
            extras.append(f"家具 {_fmt_num(deck.fixture_bonus_power)}")
        if deck.area_item_bonus_power:
            extras.append(f"区域道具 {_fmt_num(deck.area_item_bonus_power)}")
        if extras:
            lines.append(f"    （{'、'.join(extras)}）")
        if deck.event_bonus_rate:
            lines.append(f"  活动加成：{_fmt_num(deck.event_bonus_rate)}%")

        if show_cards:
            names = card_names or {}
            for i, card in enumerate(deck.cards, start=1):
                label = names.get(card.card_id) or f"#{card.card_id}"
                lines.append(f"    {i}. {label}")
        lines.append("")

    if result.event_id:
        lines.append(
            "※ 带活动加成时**打歌得分可能比不带时更低**，这不是 bug："
            "引擎会优先挑活动加成高的卡，因为活动的目标是活动点数。"
        )
    lines.append(
        "※ 这是**假设全卡满破满级**的理论最优，"
        "不是按你实际持有的卡算的。"
    )
    lines.append(f"※ 耗时 {result.cost_ms:.0f} ms")
    return "\n".join(lines)


def render_brief(result: DeckRecommendResult) -> str:
    """一行式摘要（给 MCP 工具用）。"""
    if not result.decks:
        return "没有找到合适的卡组。"
    top = result.decks[0]
    diff = DIFFICULTY_NAMES.get(result.difficulty, result.difficulty)
    return (
        f"[{diff}] 最优卡组预测得分 {_fmt_num(top.live_score)}，"
        f"综合力 {_fmt_num(top.total_power)}，"
        f"卡牌 ID：{'/'.join(str(i) for i in top.card_ids)}"
    )