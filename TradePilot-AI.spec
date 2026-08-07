# TradePilot-AI.spec — PyInstaller 打包配置
# 用法: pyinstaller TradePilot-AI.spec
# 产物: dist/TradePilot-AI.exe（单文件）

a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('static', 'static'),        # 前端页面
        ('templates', 'templates'),  # Word 模板
        ('data', 'data'),            # 演示数据
        ('.env.example', '.env.example'),  # Key 模板（setup_env 复制用）
    ],
    hiddenimports=[
        # 业务模块（uvicorn.run("main:app") 是字符串导入，需显式声明）
        'main', 'config', 'llm', 'prompts', 'trade', 'database',
        'countries', 'hs_descriptions', 'business', 'ecommerce',
        'ebay', 'export', 'market_data',
        # uvicorn 子模块
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'matplotlib.backends.backend_agg',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TradePilot-AI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 无控制台窗口（GUI 应用）
    icon=None,
)
