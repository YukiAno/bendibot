"""卡牌 / 角色 / 卡池服务。

数据规模（实测，都是**首次拉一次然后缓存**）
-------------------------------------------
===========  ==========  ============
文件          国服        说明
===========  ==========  ============
cards.json    4.3 MB      1345 张卡
gachas.json   3.2 MB      45 个卡池
gameCharacters 9 KB       26 个角色
===========  ==========  ============

搜索策略
--------
卡牌的 ``prefix``（卡名前缀）和角色的姓名都是**中文**，所以搜中文完全可行。
``search_cards`` 会先判断关键词是不是角色名：

* 是角色名 -> 返回**该角色**的卡（按稀有度降序）
* 不是     -> 按卡名前缀 / 技能名模糊搜

卡池则按当前时间过滤出**正在开**的，并显示剩余时间。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from ..models import humanize_delta, region_tz
from ..models_card import ATTR_NAMES, Card, Character, Gacha
from ..providers import Capability, find_provider, unsupported_hint
from ..textutil import match_score

#: 属性搜索关键词 -> 属性代码（支持中文颜色）
ATTR_ALIASES: dict[str, str] = {
    "蓝": "cool",
    "蓝属性": "cool",
    "cool": "cool",
    "粉": "cute",
    "红": "cute",
    "cute": "cute",
    "橙": "happy",
    "黄": "happy",
    "happy": "happy",
    "绿": "pure",
    "pure": "pure",
    "紫": "mysterious",
    "mysterious": "mysterious",
}


async def _provider(region: str, capability: Capability):
    provider = find_provider(capability, region)
    if provider is None:
        raise ValueError(unsupported_hint(capability, region))
    return provider


# ---------------------------------------------------------------- 取数


async def all_characters(region: str) -> list[Character]:
    """全部角色。"""
    provider = await _provider(region, Capability.CHARACTER)
    raw = await provider.get_characters(region)
    chars = [Character.from_master(c, region) for c in raw if isinstance(c, dict)]
    chars.sort(key=lambda c: c.id)
    return chars


async def all_cards(region: str) -> list[Card]:
    """全部卡牌（首次会拉几 MB，之后走缓存）。"""
    provider = await _provider(region, Capability.CARD)
    raw = await provider.get_cards(region)
    cards = [Card.from_master(c, region) for c in raw if isinstance(c, dict)]
    cards.sort(key=lambda c: c.id)
    return cards


async def all_gachas(region: str) -> list[Gacha]:
    """全部卡池。"""
    provider = await _provider(region, Capability.GACHA)
    raw = await provider.get_gachas(region)
    gachas = [Gacha.from_master(g, region) for g in raw if isinstance(g, dict)]
    gachas.sort(key=lambda g: g.id)
    return gachas


# ---------------------------------------------------------------- 查找


async def find_character(region: str, keyword: str) -> Character | None:
    """按名字找角色（支持中文名、罗马字、单字）。"""
    chars = await all_characters(region)
    scored: list[tuple[int, Character]] = []
    for char in chars:
        score = match_score(
            keyword,
            char.name,
            char.given_name,
            char.first_name,
            char.name_en,
        )
        if score > 0:
            scored.append((score, char))

    if not scored:
        return None
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return scored[0][1]


async def cards_of_character(
    region: str, char: Character, *, rarity: int | None = None
) -> list[Card]:
    """某个角色的卡，按稀有度降序、再按 id 降序（新卡在前）。"""
    cards = [c for c in await all_cards(region) if c.character_id == char.id]
    if rarity is not None:
        cards = [c for c in cards if c.rarity_stars == rarity]
    cards.sort(key=lambda c: (-c.rarity_stars, -c.id))
    return cards


async def search_cards(region: str, keyword: str, *, limit: int = 10) -> list[Card]:
    """按卡名前缀 / 技能名 / 角色名搜卡。"""
    cards = await all_cards(region)
    chars = {c.id: c for c in await all_characters(region)}

    scored: list[tuple[int, Card]] = []
    for card in cards:
        char = chars.get(card.character_id)
        score = match_score(
            keyword,
            card.name,
            card.skill_name,
            char.name if char else "",
        )
        if score > 0:
            scored.append((score, card))

    scored.sort(key=lambda pair: (-pair[0], -pair[1].id))
    return [c for _, c in scored[:limit]]


async def find_card(
    region: str, keyword: str, *, rarity: int | None = None
) -> list[Card]:
    """把关键词解析成**候选卡列表**（最想看的排最前）。

    给「卡面」用。识别优先级：

    1. **纯数字** → 当卡 ID 精确查（列表里每行都带 ``#ID``，方便直接复制）
    2. **角色名** → 该角色的卡，按稀有度降序、再按 id 降序（新卡在前）
    3. **卡名 / 技能名** → 模糊搜

    和 :func:`search_cards` 的区别：这里**不截断**（调用方自己取前 N 个），
    而且多了纯数字当卡 ID 的处理。
    """
    keyword = keyword.strip()
    if not keyword:
        return []

    cards = await all_cards(region)

    # 1) 纯数字 -> 卡 ID
    if keyword.isdigit():
        target = int(keyword)
        for card in cards:
            if card.id == target:
                return [card]
        return []

    # 2) 角色名
    char = await find_character(region, keyword)
    if char is not None:
        hits = [c for c in cards if c.character_id == char.id]
        if rarity is not None:
            hits = [c for c in hits if c.rarity_stars == rarity]
        hits.sort(key=lambda c: (-c.rarity_stars, -c.id))
        return hits

    # 3) 卡名 / 技能名 / 角色名 模糊搜
    chars = {c.id: c for c in await all_characters(region)}
    scored: list[tuple[int, Card]] = []
    for card in cards:
        owner = chars.get(card.character_id)
        score = match_score(
            keyword,
            card.name,
            card.skill_name,
            owner.name if owner else "",
        )
        if score > 0:
            scored.append((score, card))

    scored.sort(key=lambda pair: (-pair[0], -pair[1].id))
    result = [c for _, c in scored]
    if rarity is not None:
        result = [c for c in result if c.rarity_stars == rarity]
    return result


async def find_card_by_id(region: str, card_id: int) -> Card | None:
    """按卡 ID 精确查一张卡（不分区服 id 空间，直接全表找）。"""
    for card in await all_cards(region):
        if card.id == card_id:
            return card
    return None


#: 卡面图存在性探测的内存缓存：``url -> (时间戳, 确定的结果)``。
#:
#: 只放内存、不落盘 —— 省的是"同一个人连着查几张卡时不用重复打 CDN"，
#: 重启后重探一次毫无损失。``None``（网络不确定）**不入缓存**，
#: 因为它不代表图的真实状态，缓存了会把一时的网络抖动固化下来。
_ART_PROBE: dict[str, tuple[float, bool]] = {}
_ART_PROBE_TTL = 6 * 3600
_ART_PROBE_MAX = 3000


async def art_url_alive(url: str, *, timeout: float = 6.0) -> bool | None:
    """探测卡面图在不在（只拉前 2 KB）。

    官方资源库实测有约 5% 的卡缺 ``card_normal.webp``（新卡还没同步、
    或该卡本来就没有这一版立绘），直接发 URL 会得到一张裂图，
    所以发之前先探一次。

    **返回三态，不能只返回 bool**：

    * ``True``  —— 有，放心用
    * ``False`` —— 服务端明确回了 404，这张图确实不存在，换下一个候选
    * ``None``  —— 超时 / 网络错误，**不确定**。不能当成"没有"，
      否则网络一抖就报「这张卡没图」。这种情况直接把 URL 交给
      NapCat 去拉，它有自己的重试。

    这样最坏耗时也就一次 ``timeout``，不会因为网络差让用户等半分钟。
    """
    if not url:
        return False

    hit = _ART_PROBE.get(url)
    if hit is not None and time.time() - hit[0] < _ART_PROBE_TTL:
        return hit[1]

    from ..cache import get_client

    client = get_client()
    try:
        resp = await client.get(
            url,
            headers={
                "Range": "bytes=0-2047",
                # 全局客户端默认 Accept 是 JSON，拉图片要显式改掉
                "Accept": "image/webp,image/png,image/*,*/*",
            },
            timeout=timeout,
        )
    except Exception:
        return None

    if resp.status_code in (200, 206):
        result: bool | None = True
    elif resp.status_code == 404:
        result = False
    else:
        result = None

    if result is not None:
        if len(_ART_PROBE) >= _ART_PROBE_MAX:
            _ART_PROBE.clear()
        _ART_PROBE[url] = (time.time(), result)
    return result


async def pick_art_url(card: Card, *, after_training: bool = False) -> tuple[str, bool]:
    """挑一个卡面图地址（只要一张时用这个）。

    依次尝试：

    1. 指定版本（特训前/后）的 WebP
    2. 另一个版本的 WebP（比如要特训前但没有，就拿特训后）
    3. 指定版本的 PNG（兜底，体积 3 MB 级别）

    **探测结果不确定（网络错误）时就直接用当前候选**，不再往下试 ——
    见 :func:`art_url_alive` 的三态说明。

    :return: ``(url, 实际用的是不是特训后)``；确认全都取不到时返回 ``("", False)``
    """
    if not card.assetbundle_name:
        return "", False

    # 本身没有特训后卡面（★1/★2/生日卡）时，别白探一次
    want_after = after_training and card.has_after_training

    seen: set[str] = set()
    for url, is_after in _version_candidates(card, want_after, allow_other=True):
        if not url or url in seen:
            continue
        seen.add(url)
        alive = await art_url_alive(url)
        if alive is False:
            continue
        # True（确认有）或 None（网络不确定）都直接用
        return url, is_after
    return "", False


def _version_candidates(
    card: Card, is_after: bool, *, allow_other: bool
) -> list[tuple[str, bool]]:
    """某个版本的候选 URL，按优先级排好。

    :param allow_other: 允许退到「另一个版本」吗。**一次要发两张时必须
        设 False** —— 否则特训前 404 会退到特训后，和本来就单独要的那张
        撞成同一个 URL。
    """
    urls: list[tuple[str, bool]] = [(card.card_art_url(after_training=is_after), is_after)]
    if allow_other:
        urls.append((card.card_art_url(after_training=not is_after), not is_after))
    urls.append((card.card_art_png(after_training=is_after), is_after))
    return urls


async def pick_art_urls(card: Card, *, want: str = "both") -> list[tuple[str, bool]]:
    """挑出**要发的全部卡面图**（默认花前 + 花后各一张）。

    :param want: ``"both"`` 前后都要 / ``"before"`` 只要特训前 /
        ``"after"`` 只要特训后（这张没有特训后时自动退回特训前）
    :return: ``[(url, 是否特训后), ...]``，按特训前 → 特训后的顺序；
        一张都取不到时返回空列表
    """
    if not card.assetbundle_name:
        return []

    can_after = card.has_after_training
    if want == "after":
        versions = [True] if can_after else [False]
    elif want == "before":
        versions = [False]
    else:  # both
        versions = [False, True] if can_after else [False]

    import asyncio

    async def resolve(is_after: bool) -> tuple[str, bool] | None:
        for url, flag in _version_candidates(card, is_after, allow_other=False):
            if not url:
                continue
            alive = await art_url_alive(url)
            if alive is False:
                continue
            return url, flag
        return None

    # 两张并发探测，用户只等一次网络往返
    results = await asyncio.gather(*[resolve(v) for v in versions])

    out: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for item in results:
        if item is None:
            continue
        url, flag = item
        if url in seen:
            continue
        seen.add(url)
        out.append((url, flag))
    out.sort(key=lambda pair: pair[1])  # 特训前在前
    return out



    """正在开的卡池（``include_upcoming=True`` 时带上即将开始的）。"""
    now = datetime.now(timezone.utc)
    result = []
    for gacha in await all_gachas(region):
        status = gacha.status(now)
        if status == "running" or (include_upcoming and status == "upcoming"):
            result.append(gacha)

    # 新开的排前面
    result.sort(key=lambda g: (g.start_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return result


async def recent_gachas(region: str, limit: int = 5) -> list[Gacha]:
    """最近的卡池（含已结束的），按开始时间倒序。"""
    gachas = [g for g in await all_gachas(region) if g.start_at]
    gachas.sort(key=lambda g: g.start_at, reverse=True)
    return gachas[:limit]


# ---------------------------------------------------------------- 渲染


def render_card(card: Card, char: Character | None = None, *, compact: bool = False) -> str:
    """渲染一张卡。"""
    if compact:
        who = char.name if char else f"角色{card.character_id}"
        return f"{card.rarity_label} {who} {card.name}"

    who = char.name if char else f"角色{card.character_id}"
    lines = [f"{card.rarity_label} 【{who}】{card.name}"]
    if card.attr:
        lines.append(f"属性：{card.attr_label}")
    if card.skill_name and card.skill_name != card.name:
        lines.append(f"技能：{card.skill_name}")
    lines.append(f"实装：{card.released_text}  卡 ID：{card.id}")
    return "\n".join(lines)


def render_card_list(
    cards: list[Card],
    chars: dict[int, Character],
    *,
    title: str = "",
    limit: int = 12,
    show_owner: bool = True,
) -> str:
    """渲染卡牌列表。

    :param show_owner: 每行是否带角色名。**按角色查的时候传 False** ——
        标题里已经写了是谁，56 行每行再重复一遍「星乃一歌」纯属浪费。
    """
    if not cards:
        return "没有符合条件的卡。"

    lines: list[str] = []
    if title:
        lines.append(title)

    for card in cards[:limit]:
        # 行首带 #ID：想单独看某张的卡面，直接「卡面 <这个ID>」
        head = f"• #{card.id} {card.rarity_label}"
        if show_owner:
            char = chars.get(card.character_id)
            who = char.name if char else f"角色{card.character_id}"
            head += f" {who}"
        attr = ATTR_NAMES.get(card.attr, card.attr)
        lines.append(f"{head} {card.name}（{attr}）")

    if len(cards) > limit:
        lines.append(
            f"（共 {len(cards)} 张，只显示前 {limit} 张；"
            f"用「卡面 <卡ID>」看单张卡面）"
        )
    return "\n".join(lines)


def render_gacha(gacha: Gacha, *, now: datetime | None = None) -> str:
    """渲染一个卡池。"""
    now = now or datetime.now(timezone.utc)
    status = gacha.status(now)
    tag = {"running": "进行中", "upcoming": "未开始", "ended": "已结束"}.get(status, "?")

    lines = [f"• {gacha.name}"]
    lines.append(f"  类型：{gacha.type_label}  状态：{tag}")
    lines.append(f"  时间：{gacha.time_range_text()}")
    if status == "running":
        lines.append(f"  剩余：{gacha.remaining_text(now)}")
    return "\n".join(lines)


def render_gacha_list(gachas: list[Gacha], *, limit: int = 8) -> str:
    """渲染卡池列表。"""
    if not gachas:
        return "当前没有正在开的卡池。"
    now = datetime.now(timezone.utc)
    lines = [f"正在开的卡池（{len(gachas)} 个）："]
    for gacha in gachas[:limit]:
        lines.append("")
        lines.append(render_gacha(gacha, now=now))
    if len(gachas) > limit:
        lines.append("")
        lines.append(f"（共 {len(gachas)} 个，只显示前 {limit} 个）")
    return "\n".join(lines)


def render_character_list(chars: list[Character], *, limit: int = 30) -> str:
    """渲染角色列表（按组合分组）。"""
    if not chars:
        return "没查到角色数据。"

    groups: dict[str, list[Character]] = {}
    for char in chars:
        groups.setdefault(char.unit_label, []).append(char)

    lines = ["PJSK 角色："]
    for unit, members in groups.items():
        names = "、".join(c.name for c in members[:limit])
        lines.append(f"• {unit}：{names}")
    return "\n".join(lines)