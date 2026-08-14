## TradePilot AI v1.0.0 — 正式版

> 从"自己用的工具"升级为"能给别人演示的产品"。本版基于四路代码审查（60 条建议）落地约 47 条，覆盖安全、正确性、性能、可部署性。

### 🔒 安全加固（审查第一批全收）
- 移除宽 CORS → Host 白名单校验（防 DNS rebinding）+ 安全响应头（CSP/nosniff/frame-ancestors）
- AI 服务地址白名单校验（仅 https、拒内网/IP 字面量、域名解析逐 IP 校验）+ 请求前二次校验（纵深防御）
- DOMPurify 加载失败降级纯文本（不透传 HTML）；评论高亮改纯 DOM 操作
- 提示词注入防护：指令层级声明 + `<evidence>` 界符包裹 + 长度截断
- `.env` 写入校验（拒绝换行/# 配置注入）+ 写失败可见
- 商品采集 SSRF 加固：域名解析校验 + 逐跳重定向校验

### 🛡️ 权限层（新）
- 管理员账户：`POST /api/settings` 仅管理员可改（httpOnly 会话 cookie），未登录 401 留痕
- 匿名限流：消耗 token 的接口按 IP 滑动窗口（管理员豁免）
- 访问日志 access_log：拦截事件全部留痕，管理面板实时展示"累计拦截 X 次"
- 新页面 `admin.html`：拦截记录 / 服务器 Key 状态

### 🤖 AI Agent 一句话全流程（最强演示点）
- `POST /api/agent/run`（SSE 流式）：意图解析 → 证据链 → 市场分析 → 报告 → 客户线索 → 定制开发信
- 每步失败跳过继续，最终汇总"完成 X/6 步"；实测"蓝牙耳机去德国卖"6/6 步全通

### 🔗 客户线索模块（新）
- Tavily 多组查询词检索 + LLM 画像 + **防幻觉硬约束**（无来源 URL 一律剔除）
- 一键生成针对该公司的开发信（画像注入闭环）；页面 `leads.html`

### 🐛 正确性修复（审查第二批全收）
- CAGR 非连续年份年差修复（[2018,2020,2022] 从 ~21% 修正为 10%，单测覆盖）
- World Bank 缓存 key 加年份、竞品矩阵缓存 key 完整年份
- 组织目标（欧盟等）走成员聚合（不再全 0 却报可用）
- eBay/速卖通/财务/评论解析防御性容错

### ⚡ 性能与健壮性（审查第三四批）
- 缓存 TTL 体系：贸易数据动态 TTL（近期 90 天/旧年永久）、空结果缓存、LATEST_YEAR 30 天
- LLM 内存缓存 LRU（上限 256）+ single-flight 防并发烧 token；`?refresh=1` 强制刷新
- SQLite WAL + 启动一次性初始化 + 连接上下文管理；7 处静默吞错改日志
- LLM 重试按状态码分流（401/429/5xx）+ 结构化输出温度 0.2
- 证据链四路并行、World Bank 8 指标并发、欧盟聚合 3 线程并发

### 🧪 测试与 CI（新）
- `tests/` 50 项 pytest 全绿（统计指标/安全校验/缓存/LLM 重试/导出），无网络依赖
- GitHub Actions：ruff lint + 双 OS 测试 + PyInstaller 构建

### 🚀 部署（新）
- Dockerfile（含 LibreOffice + Noto CJK 字体，Linux PDF 导出）+ docker-compose.yml
- requirements 锁版本；DB 路径锚定（exe 模式走用户目录）

### 📋 使用
- 开发：`python -m uvicorn main:app --reload` → http://127.0.0.1:8000
- 配置：复制 `.env.example` 为 `.env`，填 DeepSeek Key + `ADMIN_PASSWORD`（不设则每次启动生成随机密码打印到日志）
- 演示：首页输入"蓝牙耳机去德国卖"一句话全流程
