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
    """确保 .env 存在：exe 运行目录有则用，没有则从同目录复制模板"""
    base = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, 'frozen', False) else __file__))
    env_path = os.path.join(base, '.env')
    if not os.path.exists(env_path):
        example = os.path.join(base, '.env.example')
        if os.path.exists(example):
            shutil.copy(example, env_path)
    # 切换到 exe 所在目录（保证相对路径资源可访问）
    if getattr(sys, 'frozen', False):
        os.chdir(base)


def find_free_port() -> int:
    """找空闲端口（默认 8000，被占用则递增）"""
    for port in range(8000, 8020):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 8000


def run_server(port: int):
    """后台启动 FastAPI（错误写入日志便于排查）"""
    try:
        import main as main_mod
        # 打印资源路径到日志（排查用）
        import sys
        log_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "tradepilot_error.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"BASE_DIR: {getattr(main_mod, 'BASE_DIR', 'N/A')}\n")
            f.write(f"_MEIPASS: {getattr(sys, '_MEIPASS', 'N/A')}\n")
            f.write(f"static exists: {os.path.exists(getattr(main_mod, 'BASE_DIR', '') + '/static')}\n")
        uvicorn.run("main:app", host="127.0.0.1", port=port, log_level="warning")
    except Exception as e:
        import traceback
        log_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "tradepilot_error.log")
        with open(log_path, "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)


def main():
    setup_env()
    port = find_free_port()
    # 后台线程启动服务器
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    import webview

    webview.create_window(
        "TradePilot AI — 跨境贸易智能平台 v0.9.1",
        f"http://127.0.0.1:{port}",
        width=1280,
        height=850,
        min_size=(960, 640),
    )
    webview.start()


if __name__ == "__main__":
    main()
