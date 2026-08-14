# TradePilot AI — 跨境贸易智能平台

> 面向消费电子出海的 **AI 市场分析与业务辅助平台**。覆盖四个业务模块：**市场分析**（产品+国家 → 结构化市场报告 + 多国横向对比 + 历史记录）、**贸易数据**（HS 编码 → 出口数据 + 趋势图 + AI 解读 + 竞争力指标 + 驱动因素分析）、**外贸业务**（英文开发信 / 跟进邮件 / 产品介绍 / AI 模拟客户）、**跨境电商**（商品采集 / 评论分析 / 竞品对比 / 平台 Listing / 财报画像 / eBay + 速卖通商品分析）。

**技术栈**：Python · FastAPI · SQLite · 原生 HTML/JS · ECharts · matplotlib（报告图表）· 多 AI 提供商（DeepSeek 默认 / GPT / Claude / 自定义）· UN Comtrade API · World Bank API · Tavily API · eBay Browse API · AliExpress 联盟开放平台 API

**报告导出**：Word（学术论文式：封面/目录/字体分级/页码/表格防切分）+ PDF，两种格式均可下载。

> ⚠️ **数据声明**：市场分析基于**多重数据证据链**（UN Comtrade 贸易数据 / World Bank 经济环境 / Tavily 行业动态 / WTO 宏观背景 / TC 竞争力指数），AI 引用并解读这些数据；统计指标（CAGR/峰值/竞争力）由程序精确计算，AI 不参与算数；数据不足处 AI 估算并标注"估算"；评论分析基于用户提供的评论样本。

---

## 快速开始（三步）

1. **配置 API Key**

   复制 `.env.example` 为 `.env`，填入 AI Key（[DeepSeek 获取地址](https://platform.deepseek.com/api_keys)，也可在页面右上角 ⚙️ 设置面板切换 GPT/Claude/自定义提供商并填对应 Key）。其余密钥可选：Tavily（行业动态/宏观背景/竞争格局）、eBay 与速卖通联盟（商品分析增强）：

   ```bash
   copy .env.example .env
   ```

2. **安装依赖**

   ```bash
   python -m venv venv
   venv\Scripts\activate        # 激活后命令行开头会出现 (venv)
   pip install -r requirements.txt
   # 安装慢可加镜像源: -i https://pypi.tuna.tsinghua.edu.cn/simple
   ```

3. **启动服务**

   ```bash
   python -m uvicorn main:app --reload
   ```

   浏览器打开 <http://127.0.0.1:8000> → 输入产品（如"蓝牙耳机"）和目标国家（如"德国"）→ 点击"开始分析"，10-30 秒后生成报告。

---

## API 文档

### `POST /api/analyze`

输入产品 + 目标国家，返回 Markdown 格式的市场分析报告。

**请求示例：**

```json
{ "product": "蓝牙耳机", "country": "德国" }
```

**响应示例（200）：**

```json
{
  "report": "# 蓝牙耳机市场分析（德国）\n\n## 市场规模\n..."
}
```

**错误响应：**

| 状态码 | 场景 |
| --- | --- |
| 400 | product 或 country 为空 |
| 502 | DeepSeek API 调用失败（超时 / 余额不足 / Key 无效 / 网络中断重试后仍失败） |

启动后可访问 <http://127.0.0.1:8000/docs> 在线调试（FastAPI 自带 Swagger UI）。

### `POST /api/analyze/compare`

产品 + 2-5 个国家 → 多国横向对比（规模/增速/竞争力/风险）+ AI 市场选择建议。

**请求示例：**

```json
{ "product": "蓝牙耳机", "countries": ["德国", "美国"] }
```

**响应（200）：** `{ product, countries, per_country: {国家: {market_context, trade_evidence, competitiveness}}, comparison: {overview, market_table, recommendations, key_insights, risks} }`

**说明**：每国独立聚合真实数据证据链（UN Comtrade 贸易趋势 + World Bank 经济环境 + TC 竞争力指标），对比表数字列全部程序精确计算，AI 只提供机会点/风险解读与入选建议，不参与算术。

### `GET /api/history`

最近 10 条查询历史（可选按类型过滤 `?report_type=market|trade`）。前端"历史"按钮调用，点击记录回填表单重新查询；同参数重复查询命中历史缓存（7 天 TTL）直接返回，不重复消耗 API。

### 报告导出（支持 Word / PDF）

- `POST /api/analyze/export`：市场分析报告，`fmt=docx|pdf`（默认 docx）
- `POST /api/trade/export/report`：贸易数据报告，`fmt=docx|pdf`

报告为学术论文式排版：封面 + 目录（点线页码 + 单击跳转）+ 章节空行 + 首行缩进 + 字体分级（黑体标题/宋体正文）+ 页脚页码 + 表格防切分 + 饼图/柱状图/折线图图表分析。

### `POST /api/ecommerce/collect`

商品 URL 或粘贴文本 → 采集商品画像 + AI 选品分析（无 Key 可采集画像，AI 分析降级跳过）。

**请求示例：**

```json
{ "url": "https://www.amazon.com/dp/B0XXXX", "pasted_text": "" }
```

**响应（200）：** `{ item: {title, brand, price, specifications, ...}, analysis: {assessment, price_position, ...} }`

**说明**：优先抓取商品页公开数据（JSON-LD）；页面不可访问时可用 `pasted_text` 粘贴页面内容，AI 提取字段（不破解反爬）。未配置 AI Key 时 URL 采集成功仍返回画像（analysis 为空），粘贴路径返回 502 提示配置。

### `POST /api/company/financials`

公司名 → 财务画像（营收/净利/毛利率/研发），SEC 美股 / A 股东方财富 / 非上市白名单兜底。

**请求示例：**

```json
{ "company": "苹果" }
```

**响应（200）：** `{ company, source, periods: [{year, revenue, net_income, ...}] }`

**说明**：三层数据源——SEC EDGAR（苹果/索尼/特斯拉/戴尔等美股）、东方财富 A 股（歌尔/立讯/漫步者等 12 家）、非上市白名单 Tavily 兜底（华为/OPPO/大疆等 8 家）。白名单外公司拒绝，杜绝幻觉数据。

### `POST /api/leads/search`（客户线索，v1.0）

产品 + 目标市场 → 潜在客户线索（Tavily 多组查询词 + LLM 画像）。

**请求示例：** `{ "product": "蓝牙耳机", "country": "德国" }`

**响应（200）：** `{ leads: [{company, business_scope, size_signal, match_reason, source_url}], disclaimer }`

**防幻觉硬约束**：无公司名 / 无来源 URL / URL 不在搜索结果中 → 一律剔除；输出带"公开检索、需人工核实"免责声明。

### `POST /api/leads/outreach`（线索闭环）

线索画像 → 针对该公司的英文开发信（画像注入 business 模块）。

### `POST /api/agent/run`（AI Agent 一句话全流程，SSE 流式，v1.0）

**请求示例：** `{ "input": "蓝牙耳机去德国卖" }`

**响应（200，text/event-stream）**：逐步推送 6 步进度事件（意图解析 → 证据链 → 市场分析 → 报告 → 线索 → 开发信），每步失败跳过继续，最后 `result` 事件含 `{report, leads, outreach, summary}`。

### 管理员（v1.0）

- `POST /api/admin/login`：密码（.env `ADMIN_PASSWORD`，未配置时启动日志打印随机密码）→ httpOnly session cookie
- `POST /api/admin/logout`、`GET /api/admin/access-log`（拦截记录，仅管理员）
- `POST /api/settings` 仅管理员可调用（未登录 401）；`GET /api/settings` 公开（只回状态不返回值）

---

## 目录结构

```
├── main.py          # FastAPI 入口：路由 + 报告渲染（含 null 兜底）+ 安全中间件
├── config.py        # 读取 .env 配置（AI/搜索/eBay/速卖通密钥，运行时热更新）+ base_url 白名单校验
├── llm.py           # 多 AI 提供商调用层：重试分流、JSON 解析、证据链注入、LRU 缓存 + single-flight
├── prompts.py       # 系统提示词（9 字段 JSON 协议，IDC/学术报告风格 + 指令层级声明）
├── trade.py         # 贸易数据模块：UN Comtrade 查询、组织聚合（并发）、统计指标、HS 编码 AI 解析
├── business.py      # 外贸业务模块：开发信 / 跟进 / 产品介绍 / 模拟客户
├── ecommerce.py     # 跨境电商模块：评论分析 / 商品画像分析 / 竞品对比 / Listing
├── collectors.py    # 商品数据采集层：URL 抓取（SSRF 逐跳校验）/ JSON-LD / 粘贴 AI 提取
├── financials.py    # 财务画像：SEC 美股 / 东方财富 A 股 / 非上市白名单兜底（带单位标注）
├── ebay.py          # eBay 商品分析（OAuth + Browse API，可选增强）
├── aliexpress.py    # 速卖通商品分析（联盟开放平台 API + HmacSHA256 签名）
├── agent.py         # AI Agent 编排层：一句话 → 市场分析 → 报告 → 线索 → 开发信（SSE 进度）
├── leads.py         # 客户线索模块：Tavily 多组查询 + LLM 画像 + 防幻觉硬约束 + 定向开发信
├── export.py        # 报告导出：Word（python-docx 程序化排版）+ PDF + CSV + 图表
├── database.py      # SQLite 缓存层（TTL/空结果缓存/WAL/访问日志/管理员会话）
├── market_data.py   # 多数据源：World Bank 经济（并发拉取）+ Tavily 搜索 + WTO 宏观背景 + 竞争格局
├── desktop.py       # 桌面版入口（PyWebView）
├── countries.py     # 完整国家清单（159 项 + 组织代码）
├── hs_descriptions.py # HS 编码品名描述
├── data/
│   ├── sample_products.json  # 商品采集演示画像
│   ├── sample_reviews.json   # 旧演示评论数据
│   └── samples/              # 18 品类真实评论样本库（McAuley 公开数据集）
├── scripts/         # 工具脚本（样本构建/清洗/测试脚本，不打包发行版）
├── tests/           # pytest 单元测试（统计指标/安全校验/缓存/LLM/导出，无网络）
├── static/          # 前端（六页面 + 主题切换 + 书签工具）
│   ├── index.html       # 市场分析 + AI Agent 一句话全流程（首页）
│   ├── trade.html       # 贸易数据
│   ├── business.html    # 开发信
│   ├── ecommerce.html   # 跨境电商
│   ├── leads.html       # 客户线索
│   ├── admin.html       # 管理面板（拦截记录 / Key 状态）
│   ├── app.js           # 市场分析页逻辑（单国/多国分流）
│   ├── bookmarklet.js   # 一键复制评论书签工具
│   ├── theme.js         # 双主题切换（☀️/🌙）
│   ├── settings.js      # 设置面板（API Key / AI 提供商 / 管理员登录）
│   └── style.css        # 设计系统（亮色/暗色）
├── requirements.txt   # 运行时依赖（锁版本）
├── requirements-dev.txt  # 测试/构建工具（pytest/ruff/pyinstaller）
├── requirements-win.txt # Windows 专属（pywin32/pywebview）
└── .env             # 密钥（已被 .gitignore 排除，不提交）
```

**分层设计**：`config → llm → main → 业务模块（trade/business/ecommerce/ebay/aliexpress）`，`llm.py` / `prompts.py` 是所有模块共用的底座。

---

## 路线图

| 阶段 | 模块 | 内容 |
| --- | --- | --- |
| ✅ 已完成 | 市场分析 Research | 产品 + 国家 → 结构化市场报告（9 字段：摘要五段式/规模/趋势/品牌点评/画像/法规风险/行动路线/展望） |
| ✅ 已完成 | 贸易数据 Trade | HS 编码 → 中国出口数据 + 趋势图 + AI 解读 + Word/CSV 导出 |
| ✅ 已完成 | 外贸业务 Business | 英文开发信（含真实数据引用）/ 跟进邮件 / 产品介绍+FAQ / AI 模拟客户 |
| ✅ 已完成 | 跨境电商 E-commerce | 商品采集（URL/粘贴）+ 评论分析 → 痛点报告 + 竞品对比 + 平台 Listing + 一键书签 |
| ✅ 已完成 | 商品分析 | eBay（OAuth + Browse API）与速卖通（联盟开放平台 API）商品链接 → 商品信息 + AI 采购建议（可选增强） |
| ✅ 已完成 | 财务画像 | SEC 美股 / 东方财富 A 股 / 非上市白名单兜底 → 供应商/竞品财报画像 |
| ✅ 已完成 | 数据证据链 | 市场分析/贸易数据接入 5 层真实数据（UN Comtrade + World Bank + Tavily + WTO 宏观背景 + TC 竞争力指标） |
| ✅ 已完成 | 多国市场对比 | 2-5 国横向对比（出口额/CAGR/TC/份额 + AI 入选建议），市场分析页高级选项 |
| ✅ 已完成 | 报告升级 | Word/PDF 双格式：学术论文式排版 + 图表分析（饼图/柱状/折线）+ 驱动因素（CPI 趋势）+ 龙头财报画像 |
| ✅ 已完成 | 历史记录 | 两页"历史"按钮：最近 10 条查询回看 + 同参数命中缓存（7 天 TTL，省 token） |
| ✅ 已完成 | 设置与桌面版 | 页面设置面板（AI 提供商/搜索源/Key 即时生效）+ PyWebView 桌面版入口 |
| 远期 | AI Agent | 一句话 → 自动完成 查市场→查竞品→生成报告→生成开发信 全流程 |

**演进方式**：新模块以独立业务模块（market_data / ebay 等）扁平扩展，`main.py` 保持路由入口；AI 调用底座（`llm.py` / `prompts.py`）复用。

---

## 常见问题

| 问题 | 解决 |
| --- | --- |
| `venv\Scripts\activate` 报错 | 确认用反斜杠路径；提示符出现 `(venv)` 才算激活成功 |
| 中文乱码 | 所有源码文件保持 UTF-8 编码；终端执行 `chcp 65001` |
| 提示符不出现 `(venv)` | 在项目根目录执行，不要在别的目录激活 |
| 502 错误 | 检查 .env 中 Key 是否正确、账户余额是否充足 |
| 开着梯子导致请求失败 | 已内置强制直连（`proxies=None`），理论上无需处理；若仍失败，确认 DeepSeek 域名未被代理拦截 |
| 端口被占用 | 启动命令加 `--port 8001` |

> 🔒 **安全**：API Key 只存在 `.env` 中，已被 `.gitignore` 排除。若不小心提交了 Key，请立即到 DeepSeek 后台重置。
