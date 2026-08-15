"""desktop.py — TradePilot AI 桌面版入口

PyWebView 包住 FastAPI 后端 + 前端页面：
1. 后台线程启动 uvicorn（端口 8000 或自动递增）
2. 桌面窗口加载 http://127.0.0.1:port
3. 窗口关闭时退出

打包：pyinstaller TradePilot-AI.spec（产物 dist/TradePilot-AI.exe）
"""
import os
import socket
import sys
import threading
import shutil

import uvicorn


def setup_env():
    """确保 .env 存在：exe 运行目录有则用，没有则从模板复制

    模板来源优先级：exe 同目录 > _MEIPASS 内部（PyInstaller 打包资源）> 源码目录
    """
    base = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, 'frozen', False) else __file__))
    env_path = os.path.join(base, '.env')
    if not os.path.exists(env_path):
        # 找模板：先 exe 同目录，再 _MEIPASS（打包进 exe 的），最后源码目录
        candidates = [
            os.path.join(base, '.env.example'),
            os.path.join(getattr(sys, '_MEIPASS', ''), '.env.example'),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env.example'),
        ]
        for example in candidates:
            if example and os.path.exists(example):
                shutil.copy(example, env_path)
                break
    # 切换到 exe 所在目录（保证相对路径资源可访问）
    if getattr(sys, 'frozen', False):
        os.chdir(base)


def find_free_port() -> int | None:
    """找空闲端口：让 OS 分配（bind 0 → getsockname 读回）

    回归修复：原实现 8000-8019 逐个 bind→close→return，存在 TOCTOU 竞态
    （探测后端口被抢），且 20 个端口全占用时静默回退到必失败的 8000。
    现方案无竞态、无范围限制；失败（系统无端口）返回 None 由调用方报错。
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
    except OSError:
        return None


def run_server(port: int):
    """后台启动 FastAPI（错误写入日志便于排查）"""
    try:
        import main as main_mod
        # 打印资源路径到日志（排查用）
        log_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "tradepilot_error.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"BASE_DIR: {getattr(main_mod, 'BASE_DIR', 'N/A')}\n")
            f.write(f"_MEIPASS: {getattr(sys, '_MEIPASS', 'N/A')}\n")
            f.write(f"static exists: {os.path.exists(getattr(main_mod, 'BASE_DIR', '') + '/static')}\n")
        # 回归修复：直接传 app 对象（"main:app" 字符串在 PyInstaller 下偶发导入失败）
        uvicorn.run(main_mod.app, host="127.0.0.1", port=port, log_level="warning")
    except Exception as e:
        import traceback
        log_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "tradepilot_error.log")
        with open(log_path, "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)


def _wait_ready(port: int, timeout: float = 30.0) -> bool:
    """轮询服务就绪（回归修复：原立即建窗，uvicorn 冷启动数百毫秒期间
    用户看到连接失败页）"""
    import time
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/settings", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def _show_error(title: str, msg: str):
    """弹窗报错（Windows），失败时退回日志"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, title, 0x10)  # MB_ICONERROR
    except Exception:
        log_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "tradepilot_error.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[FATAL] {title}: {msg}\n")


def main():
    setup_env()
    port = find_free_port()
    if port is None:
        _show_error("端口不足", "8000-8019 端口均被占用，无法启动服务。请关闭占用端口的程序后重试。")
        return
    # 后台线程启动服务器
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    if not _wait_ready(port):
        _show_error("服务启动失败",
                    "TradePilot 服务未能在 30 秒内就绪，请查看 tradepilot_error.log 后重试。")

    import webview

    webview.create_window(
        "TradePilot AI — 跨境贸易智能平台 v1.0",
        f"http://127.0.0.1:{port}",
        width=1280,
        height=850,
        min_size=(960, 640),
    )
    webview.start()


if __name__ == "__main__":
    main()
