"""文本处理小工具 —— 中文/日文/英文混合搜索的归一化。

为什么需要
----------
master data 里的名字五花八门：

* 日文：``ロキ``、``テオ``、``初音ミクの消失``
* 罗马字：``Tell Your World``、``METEOR``
* 中文（角色名、卡名前缀）：``星乃一歌``、``外表帅气且为朋友着想``

用户输入更随意：全角半角混用、多打空格、加不加标点都可能。
所以搜索前统一做一次 **NFKC 归一化 + 去空白标点 + 转小写**，
这样「Tell your world」「TellYourWorld」「ｔｅｌｌ　ｙｏｕｒ　ｗｏｒｌｄ」都能命中。
"""

from __future__ import annotations

import re
import unicodedata

#: 要去掉的空白与标点（含中日文标点）
_STRIP_RE = re.compile(
    r"[\s\u3000_\-‐‑‒–—―~〜～!！?？.。,，、;；:：'\"“”‘’()（）\[\]【】{}｛｝<>《》/／\\|｜+＋*＊&＆@＠#＃]+"
)


def normalize(text: str) -> str:
    """归一化：全角转半角、去空格标点、统一小写。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = _STRIP_RE.sub("", text)
    return text.lower()


def match_score(query: str, *candidates: str) -> int:
    """给一组候选文本打分，返回最高分（0 = 不匹配）。

    优先级：完全相同 > 开头匹配 > 包含匹配。

    :param query: 用户输入
    :param candidates: 待匹配的文本（越靠前的权重越高）
    """
    key = normalize(query)
    if not key:
        return 0

    best = 0
    total = len(candidates)
    for index, text in enumerate(candidates):
        # 靠前的候选权重高：第一个 100 分，之后每个递减
        base = 100 - index * 10
        if base < 20:
            base = 20

        norm = normalize(text)
        if not norm:
            continue

        if norm == key:
            score = base + 50
        elif norm.startswith(key):
            score = base + 30
        elif key in norm:
            score = base + 10
        else:
            continue

        if score > best:
            best = score
    return best


def contains_any(text: str, keywords: list[str]) -> bool:
    """文本是否包含任一关键词（归一化后）。"""
    norm = normalize(text)
    return any(normalize(k) in norm for k in keywords if k)