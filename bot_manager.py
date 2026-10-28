# -*- coding: utf-8 -*-
"""bendibot 进程管理工具。

为什么需要它
------------
Windows 下直接点控制台窗口右上角的 X 关闭时，Python / uvicorn 进程有时
不会随之退出，会变成后台残留进程继续占着 8080 端口。下次启动就会报：

    [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8080)

本脚本负责把这些残留清理干净，并提供正确的停止方式。

用法
----
    python bot_manager.py clean    清理占用 8080 的残留进程（启动前后自动调用）
    python bot_manager.py stop     停止机器人
    python bot_manager.py status   查看端口占用情况

安全设计
--------
只结束**占用 8080 且进程名是 python.exe** 的进程。
如果发现占用者是别的程序，会跳过并提示，绝不误杀。
"""

import subprocess
import sys
import time
from pathlib import Path

PORT = 8080
BASE = Path(__file__).resolve().parent

OK = "[OK]"
BAD = "[!!]"
WARN = "[?]"


def _run(cmd):
    """执行命令并用 GBK 解码输出（Windows 自带命令多为 GBK）。"""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="gbk",
            errors="replace",
            timeout=20,
        )
        return (r.stdout or "") + (r.stderr or "")
    except Exception:
        return ""


def find_listeners(port=PORT):
    """返回正在 LISTENING 指定端口的 PID 列表。"""
    pids = []
    for line in _run(["netstat", "-ano"]).splitlines():
        parts = line.split()
        if (
            len(parts) >= 5
            and parts[0].upper() == "TCP"
            and parts[3].upper() == "LISTENING"
            and parts[1].endswith(":" + str(port))
        ):
            try:
                pids.append(int(parts[4]))
            except ValueError:
                pass
    return sorted(set(pids))


def proc_name(pid):
    """取进程名；取不到返回空字符串。"""
    out = _run(["tasklist", "/FI", "PID eq {}".format(pid), "/FO", "CSV", "/NH"])
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('"'):
            return line.split('","')[0].strip('"')
    return ""


def clean(verbose=True):
    """清理占用 8080 的残留进程。返回 True 表示端口最终可用。"""
    pids = find_listeners()

    if not pids:
        if verbose:
            print("  8080 没有被占用，无需清理  " + OK)
        return True

    for pid in pids:
        name = proc_name(pid)
        low = name.lower()

        if low and low != "python.exe":
            # 不是 Python，可能是别的服务，不碰它
            print("  [跳过] PID={} 是 {}，不是 Python 进程".format(pid, name))
            print("         不会自动结束它。若确认是残留进程，请用任务管理器手动结束。")
            return False

        label = name or "python.exe"
        print("  [结束] PID={}  ({})  正在占用 8080".format(pid, label))
        _run(["taskkill", "/F", "/PID", str(pid)])

    # 等待并复查
    for _ in range(10):
        time.sleep(0.5)
        if not find_listeners():
            if verbose:
                print("  8080 已释放，可以正常启动  " + OK)
            return True

    left = find_listeners()
    print("  [失败] 仍被占用: {}  {}".format(left, BAD))
    print("         请打开任务管理器，手动结束这些 PID 对应的 python.exe")
    return False


def status():
    """打印端口占用情况。"""
    pids = find_listeners()
    print()
    print("  端口 {} 状态:".format(PORT))
    if not pids:
        print("    没有进程监听  " + OK + "（机器人未运行）")
    else:
        for pid in pids:
            print("    PID={}  {}  正在监听  {}".format(pid, proc_name(pid) or "?", OK))

    # 顺带看看有没有已建立的连接（NapCat 是否连上）
    est = 0
    for line in _run(["netstat", "-ano"]).splitlines():
        parts = line.split()
        if len(parts) >= 5 and ":{}".format(PORT) in parts[1] and parts[3].upper() == "ESTABLISHED":
            est += 1
    print("  已建立的连接数: {}".format(est))
    if est:
        print("    NapCat 已连上机器人  " + OK)
    elif pids:
        print("    NapCat 尚未连接  " + BAD + "（检查 NapCat 窗口是否开着）")
    print()


def main():
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "clean").strip().lower()

    if cmd in ("clean", "stop"):
        title = "停止 bendibot" if cmd == "stop" else "bendibot 启动前清理"
        print("=" * 52)
        print("  " + title)
        print("=" * 52)
        ok = clean()
        print("=" * 52)
        if cmd == "stop":
            if ok:
                print("  机器人已停止。")
            else:
                print("  未能完全停止，请看上面的提示。")
        print()
        return 0 if ok else 1

    if cmd == "status":
        print("=" * 52)
        print("  bendibot 状态")
        print("=" * 52)
        status()
        print("=" * 52)
        return 0

    print("用法: python bot_manager.py [clean|stop|status]")
    return 2


if __name__ == "__main__":
    sys.exit(main())