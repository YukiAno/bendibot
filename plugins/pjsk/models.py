"""数据模型 —— 把各区服 master data 的字段差异收敛到这里。

为什么要有这一层
----------------
各区服的 master data 字段几乎一样，但有几个坑：

* **时间格式**：统一是**字符串** ``"2026-10-21T15:00:00+09:00"``（带时区偏移），
  不是 Unix 时间戳。国服是 ``+08:00``，日服是 ``+09:00``。
* **活动结束字段**：master data 里**没有 ``endAt``**！真正能用的是
  ``aggregateAt``（集结结束，即活动实际结束时间）。
* **名字字段**：国服的活动名是中文，日服是日文，同一个活动中文名会晚一期。

所以模型层只做一件事：**把各个源拿到的原始 dict 变成统一的、带时区的 datetime**，
上层（services / commands）就不用再关心区服差异了。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

#: 各区服时区偏移（小时）。国服 +8，日服 +9，韩服 +9，台服 +8，国际服按 UTC -7 近似。
REGION_TZ_OFFSET: dict[str, int] = {
    "cn": 8,
    "jp": 9,
    "tw": 8,
    "kr": 9,
    "en": -7,
}


def region_tz(region: str) -> timezone:
    """取区服对应的时区对象。"""
    hours = REGION_TZ_OFFSET.get(region, 9)
    return timezone(timedelta(hours=hours))


def parse_dt(value: str | int | float | None, region: str) -> datetime | None:
    """把 master data 的时间字符串解析成**带时区**的 datetime。

    支持三种写法：

    * ISO 8601 带偏移：``2026-10-21T15:00:00+09:00``
    * ISO 8601 无偏移：``2026-10-21T15:00:00`` -> 按区服时区解释
    * Unix 时间戳（秒或毫秒）
    """
    if value is None or value == "":
        return None

    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e11:  # 毫秒
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    text = str(value).strip()
    if not text:
        return None

    # 纯数字字符串也当时间戳
    if text.isdigit():
        return parse_dt(int(text), region)

    iso = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        # 再试几种常见格式
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=region_tz(region))
    return dt


def humanize_delta(delta: timedelta) -> str:
    """把时间差变成「3天5小时」「2小时13分」这种人话。"""
    total = int(delta.total_seconds())
    if total < 0:
        return "已结束"

    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60

    if days:
        # 超过 1 天时，分钟不重要，省掉更清爽
        return f"{days}天{hours}小时" if hours else f"{days}天"
    if hours:
        return f"{hours}小时{minutes}分" if minutes else f"{hours}小时"
    if minutes:
        return f"{minutes}分"
    return "不到1分钟"


@dataclass
class Event:
    """活动（已归一化，与区服无关）。"""

    id: int
    name: str
    region: str
    start_at: datetime | None = None
    #: 集结结束时间 —— master data 里**没有** endAt，用这个当结束
    aggregate_at: datetime | None = None
    #: 排行榜公布
    ranking_announce_at: datetime | None = None
    #: 活动关闭（可查最终结果）
    closed_at: datetime | None = None
    event_type: str | None = None
    unit: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    # -------------------------------------------------- 状态判断

    def status(self, now: datetime | None = None) -> str:
        """返回 ``upcoming`` / ``running`` / ``ended``。"""
        now = now or datetime.now(timezone.utc)

        if self.start_at and now < self.start_at:
            return "upcoming"
        if self.aggregate_at and now >= self.aggregate_at:
            return "ended"
        if self.start_at and now >= self.start_at:
            return "running"
        return "unknown"

    @property
    def is_running(self) -> bool:
        return self.status() == "running"

    # -------------------------------------------------- 时间文案

    def remaining(self, now: datetime | None = None) -> timedelta | None:
        """距离结束还有多久（已结束返回负值）。"""
        if not self.aggregate_at:
            return None
        now = now or datetime.now(timezone.utc)
        return self.aggregate_at - now

    def remaining_text(self, now: datetime | None = None) -> str:
        """剩余时间的中文描述。"""
        left = self.remaining(now)
        if left is None:
            return "未知"
        if left.total_seconds() <= 0:
            return "已结束"
        return humanize_delta(left)

    def start_text(self, local: bool = True) -> str:
        """开始时间文案。``local=True`` 用区服本地时间。"""
        if not self.start_at:
            return "未知"
        dt = self.start_at if local else self.start_at.astimezone(timezone.utc)
        if local:
            dt = dt.astimezone(region_tz(self.region))
            return dt.strftime("%Y-%m-%d %H:%M")
        return dt.strftime("%Y-%m-%d %H:%M UTC")

    def end_text(self, local: bool = True) -> str:
        """结束时间文案。"""
        if not self.aggregate_at:
            return "未知"
        dt = self.aggregate_at
        if local:
            dt = dt.astimezone(region_tz(self.region))
            return dt.strftime("%Y-%m-%d %H:%M")
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def progress(self, now: datetime | None = None) -> float | None:
        """活动进度 0~1。"""
        if not (self.start_at and self.aggregate_at):
            return None
        total = (self.aggregate_at - self.start_at).total_seconds()
        if total <= 0:
            return None
        now = now or datetime.now(timezone.utc)
        return max(0.0, min(1.0, (now - self.start_at).total_seconds() / total))

    # -------------------------------------------------- 构造

    @classmethod
    def from_master(cls, raw: dict, region: str) -> Event:
        """从 master data 的一条记录构造。"""
        return cls(
            id=int(raw.get("id", 0)),
            name=str(raw.get("name", "")),
            region=region,
            start_at=parse_dt(raw.get("startAt"), region),
            aggregate_at=parse_dt(raw.get("aggregateAt"), region),
            ranking_announce_at=parse_dt(raw.get("rankingAnnounceAt"), region),
            closed_at=parse_dt(raw.get("closedAt"), region),
            event_type=raw.get("eventType"),
            unit=raw.get("unit"),
            raw=raw,
        )


#: 歌曲分类代码 -> 中文标签
MUSIC_CATEGORY_NAMES: dict[str, str] = {
    "mv": "3DMV",
    "mv_2d": "2DMV",
    "original": "原创曲",
    "another_vocal": "另一个世界",
    "virtual_singer": "虚拟歌手",
    "sekai": "世界 ver.",
}

#: 区服 -> 资源库代号。实测资源库只有 jp 和 cn 两套，
#: 台服/韩服/国际服直接复用日服的资源（立绘是同一套）。
ASSET_REGIONS: dict[str, str] = {
    "cn": "cn",
    "jp": "jp",
    "tw": "jp",
    "kr": "jp",
    "en": "jp",
}


@dataclass
class BorderLine:
    """一条榜线记录：某个排名档位当前的分数线。

    ``api.sekai.best`` 的 ``/event/live`` 一次返回**全部档位**（实测日服 119 条），
    从 ``rank=300000`` 一直到 ``rank=1``，1~100 名是逐名给出的。
    """

    rank: int
    score: int
    region: str
    event_id: int | None = None
    timestamp: datetime | None = None

    @property
    def score_text(self) -> str:
        """带千分位的分数，方便读。"""
        return f"{self.score:,}"

    @property
    def rank_text(self) -> str:
        """档位文案：``100`` / ``1000`` …"""
        return f"{self.rank:,}"

    def updated_text(self) -> str:
        """数据更新时间（区服本地时间）。"""
        if not self.timestamp:
            return "未知"
        dt = self.timestamp.astimezone(region_tz(self.region))
        return dt.strftime("%m-%d %H:%M")

    @classmethod
    def from_api(cls, raw: dict, region: str) -> BorderLine:
        """从 ``/event/live`` 的一条记录构造。"""
        score_raw = raw.get("score", 0)
        try:
            score = int(score_raw)
        except (TypeError, ValueError):
            score = 0

        ts = raw.get("timestamp")
        timestamp = None
        if ts:
            try:
                timestamp = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                timestamp = None

        return cls(
            rank=int(raw.get("rank", 0) or 0),
            score=score,
            region=region,
            event_id=raw.get("eventId"),
            timestamp=timestamp,
        )


@dataclass
class Music:
    """歌曲（master data ``musics.json``）。

    实测发现两个坑：

    1. ``categories`` 在不同区服结构不同 —— 国服是
       ``[{"musicCategoryName": "mv"}]``（dict 列表），日服直接是 ``[]``。
       所以 :meth:`from_master` 做了兼容解析成纯字符串列表。
    2. **歌名都是日文或罗马字**（国服也一样，例如「初音ミクの消失」），
       所以搜中文需要**别名**支持，见 ``services/music.py``。
    """

    id: int
    title: str
    region: str
    pronunciation: str = ""
    composer: str = ""
    lyricist: str = ""
    arranger: str = ""
    categories: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    assetbundle_name: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def unit_label(self) -> str:
        """把 categories 翻成中文标签。"""
        return "/".join(MUSIC_CATEGORY_NAMES.get(c, c) for c in self.categories) or "—"

    @property
    def published_text(self) -> str:
        """发行时间的区服本地时间文案。"""
        if not self.published_at:
            return "未知"
        dt = self.published_at.astimezone(region_tz(self.region))
        return dt.strftime("%Y-%m-%d")

    def jacket_url(self, *, webp: bool = True) -> str:
        """曲绘（歌曲封面图）地址。

        实测规则（``assetbundleName`` 本身就是 ``jacket_s_001`` 这种形式，
        所以路径里会出现两次）::

            https://storage.sekai.best/sekai-{asset_region}-assets
                /music/jacket/{assetbundleName}/{assetbundleName}.{ext}

        资源库只有 **jp** 和 **cn** 两套（台服/韩服/国际服直接用 jp 的）。
        WebP 比 PNG 小得多（实测 161 KB vs 898 KB），所以默认取 WebP。

        :param webp: 取 WebP（默认）；``False`` 则取 PNG
        """
        if not self.assetbundle_name:
            return ""
        asset_region = ASSET_REGIONS.get(self.region, "jp")
        ext = "webp" if webp else "png"
        name = self.assetbundle_name
        return (
            f"https://storage.sekai.best/sekai-{asset_region}-assets"
            f"/music/jacket/{name}/{name}.{ext}"
        )

    @property
    def jacket_png(self) -> str:
        """曲绘 PNG 地址（确保兼容性时用）。"""
        return self.jacket_url(webp=False)

    @classmethod
    def from_master(cls, raw: dict, region: str) -> Music:
        # categories 兼容三种写法：dict 列表 / 字符串列表 / 空
        categories: list[str] = []
        for item in raw.get("categories") or []:
            if isinstance(item, dict):
                name = item.get("musicCategoryName") or item.get("name")
                if name:
                    categories.append(str(name))
            elif isinstance(item, str):
                categories.append(item)

        return cls(
            id=int(raw.get("id", 0)),
            title=str(raw.get("title", "")),
            region=region,
            pronunciation=str(raw.get("pronunciation", "") or ""),
            composer=str(raw.get("composer", "") or ""),
            lyricist=str(raw.get("lyricist", "") or ""),
            arranger=str(raw.get("arranger", "") or ""),
            categories=categories,
            published_at=parse_dt(raw.get("publishedAt"), region),
            assetbundle_name=str(raw.get("assetbundleName", "") or ""),
            raw=raw,
        )


#: 难度代码 -> 中文名（顺序即显示顺序）
DIFFICULTY_NAMES: dict[str, str] = {
    "easy": "Easy",
    "normal": "Normal",
    "hard": "Hard",
    "expert": "Expert",
    "master": "Master",
    "append": "Append",
}

#: 难度代码 -> 中文别名（方便用户输入）
DIFFICULTY_ALIASES: dict[str, str] = {
    "e": "easy",
    "ez": "easy",
    "easy": "easy",
    "简单": "easy",
    "n": "normal",
    "nm": "normal",
    "normal": "normal",
    "普通": "normal",
    "h": "hard",
    "hd": "hard",
    "hard": "hard",
    "困难": "hard",
    "ex": "expert",
    "expert": "expert",
    "专家": "expert",
    "m": "master",
    "ma": "master",
    "mas": "master",
    "mst": "master",
    "ms": "master",
    "master": "master",
    "大师": "master",
    "ap": "append",
    "apd": "append",
    "append": "append",
}


@dataclass
class ChartInfo:
    """一首歌某个难度的谱面信息。"""

    music_id: int
    difficulty: str
    level: int
    note_count: int
    region: str

    @property
    def difficulty_label(self) -> str:
        return DIFFICULTY_NAMES.get(self.difficulty, self.difficulty)

    @classmethod
    def from_master(cls, raw: dict, region: str) -> ChartInfo:
        return cls(
            music_id=int(raw.get("musicId", 0)),
            difficulty=str(raw.get("musicDifficulty", "")),
            level=int(raw.get("playLevel", 0) or 0),
            note_count=int(raw.get("totalNoteCount", 0) or 0),
            region=region,
        )