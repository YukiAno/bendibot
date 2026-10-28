"""缓存与取数工具 —— 统一「先缓存、后网络」的流程。

为什么必须有缓存
----------------
PJSK 的静态数据不小：国服 ``gachas.json`` 有 3.2MB、``cards.json`` 4.3MB，
日服 ``gachas.json`` 更是 **47.9MB**。如果每次指令都重新拉一遍，
一次查询要等几十秒、还可能被对方限流。所以：

* **内存层** —— 同一进程内重复查询直接命中，零延迟
* **磁盘层** —— 重启后仍有效，按 TTL 判断新鲜度
* **并发合并** —— 同一个 key 同时被多次请求时只发一次网络请求

TTL 由调用方决定（见 ``config.cfg``）：活动 1 小时、榜线 10 分钟、静态数据 1 天。

缓存文件放在 ``data/pjsk/`` 下，**可以直接删**；删了下次查询会自动重建。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
from nonebot import logger

from .config import cfg

#: 内存缓存 key -> (写入时间戳, 数据)
_MEM: dict[str, tuple[float, Any]] = {}

#: 每个 key 一把锁，避免并发重复请求
_LOCKS: dict[str, asyncio.Lock] = {}

#: 复用的 HTTP 客户端（连接池）
_CLIENT: httpx.AsyncClient | None = None

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


def _safe_name(key: str) -> str:
    """把缓存 key 变成安全的文件名。"""
    for ch in '<>:"/\\|?*':
        key = key.replace(ch, "_")
    return key


def cache_path(key: str) -> Path:
    """缓存文件路径（供调试/手动清理用）。"""
    return cfg.cache_dir / (_safe_name(key) + ".json")


def get_client() -> httpx.AsyncClient:
    """取全局 HTTP 客户端（懒初始化）。"""
    global _CLIENT
    if _CLIENT is None or _CLIENT.is_closed:
        _CLIENT = httpx.AsyncClient(
            timeout=cfg.http_timeout,
            headers=_DEFAULT_HEADERS,
            follow_redirects=True,
        )
    return _CLIENT


async def close_client() -> None:
    """关闭全局客户端（插件卸载时调用）。"""
    global _CLIENT
    if _CLIENT is not None and not _CLIENT.is_closed:
        await _CLIENT.aclose()
    _CLIENT = None


def read_cache(key: str, ttl: float, *, ignore_ttl: bool = False) -> Any | None:
    """读缓存。

    :param ttl: 有效期（秒）；``ignore_ttl=True`` 时不检查过期
    :return: 命中返回数据，未命中/过期/损坏返回 None
    """
    now = time.time()

    hit = _MEM.get(key)
    if hit is not None and (ignore_ttl or now - hit[0] < ttl):
        return hit[1]

    path = cache_path(key)
    if not path.exists():
        return None

    mtime = path.stat().st_mtime
    if not ignore_ttl and now - mtime >= ttl:
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("[pjsk] 缓存损坏 {}: {}", key, exc)
        return None

    _MEM[key] = (mtime, data)
    return data


def write_cache(key: str, data: Any) -> None:
    """写缓存（内存 + 磁盘）。写盘失败不影响正常返回。"""
    _MEM[key] = (time.time(), data)
    path = cache_path(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("[pjsk] 写缓存失败 {}: {}", key, exc)


def clear_cache(key: str | None = None) -> int:
    """清缓存；``key=None`` 时清全部。返回清掉的条数。"""
    global _MEM
    if key is None:
        count = len(_MEM)
        _MEM = {}
        removed = 0
        if cfg.cache_dir.exists():
            for f in cfg.cache_dir.glob("*.json"):
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        return max(count, removed)

    _MEM.pop(key, None)
    path = cache_path(key)
    if path.exists():
        try:
            path.unlink()
            return 1
        except OSError:
            return 0
    return 0


async def fetch_json(
    url: str,
    *,
    cache_key: str,
    ttl: float,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    json_body: Any = None,
) -> Any:
    """取 JSON：先查缓存，未命中再请求并写缓存。

    :raises httpx.HTTPError: 网络失败
    :raises ValueError: 响应不是合法 JSON
    """
    cached = read_cache(cache_key, ttl)
    if cached is not None:
        return cached

    lock = _LOCKS.setdefault(cache_key, asyncio.Lock())
    async with lock:
        # 双重检查：等锁期间可能已经有人填好了
        cached = read_cache(cache_key, ttl)
        if cached is not None:
            return cached

        client = get_client()
        logger.debug("[pjsk] 请求 {}", url)
        if method.upper() == "POST":
            resp = await client.post(url, headers=headers, json=json_body)
        else:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()

        try:
            data = resp.json()
        except Exception as exc:
            raise ValueError(f"响应不是合法 JSON: {exc}") from exc

        write_cache(cache_key, data)
        return data


async def fetch_text(
    url: str,
    *,
    cache_key: str,
    ttl: float,
    headers: dict[str, str] | None = None,
) -> str:
    """取文本（用于 ``.js`` / ``.csv`` 这类非 JSON 资源）。"""
    cached = read_cache(cache_key, ttl)
    if isinstance(cached, str):
        return cached

    lock = _LOCKS.setdefault(cache_key, asyncio.Lock())
    async with lock:
        cached = read_cache(cache_key, ttl)
        if isinstance(cached, str):
            return cached

        client = get_client()
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        text = resp.text
        write_cache(cache_key, text)
        return text