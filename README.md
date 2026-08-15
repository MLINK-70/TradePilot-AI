# TradePilot AI — 跨境贸易智能平台

> 面向消费电子出海的 **AI 市场分析与业务辅助平台**（v1.0.1 稳定版）。核心能力：**🤖 AI Agent 一句话全流程**（"蓝牙耳机去德国卖" → 自动完成 市场分析 → 报告 → 客户线索 → 定制开发信，SSE 实时进度）；另有五个业务模块：**市场分析**（产品+国家 → 结构化市场报告 + 多国横向对比 + 历史记录）、**贸易数据**（HS 编码 → 出口数据 + 趋势图 + AI 解读 + 竞争力指标）、**外贸业务**（英文开发信 / 跟进邮件 / 产品介绍 / AI 模拟客户）、**跨境电商**（商品采集 / 评论分析 / 竞品对比 / Listing / 财报画像 / eBay + 速卖通商品分析）、**客户线索**（Tavily 检索 + 防幻觉硬约束 + 定向开发信），以及**管理面板**（安全拦截记录 / 服务器 Key 状态）。

**技术栈**：Python · FastAPI · SQLite（WAL）· 原生 HTML/JS · ECharts · matplotlib（报告图表）· 多 AI 提供商（DeepSeek 默认 / GPT / Claude / 自定义）· UN Comtrade API · World Bank API · Tavily API · eBay Browse API · AliExpress 联盟开放平台 API · SSE 流式推送 · pytest / GitHub Actions CI · Docker

**报告导出**：Word（学术论文式：封面/目录/字体分级/页码/表格防切分）+ PDF（Word COM / LibreOffice 双引擎），市场分析、贸易数据、Agent 报告均可下载。

> ⚠️ **数据声明**：市场分析基于**多重数据证据链**（UN Comtrade 贸易数据 / World Bank 经济环境 / Tavily 行业动态 / WTO 宏观背景 / TC 竞争力指数），AI 引用并解读这些数据；统计指标（CAGR/峰值/竞争力）由程序精确计算，AI 不参与算数；数据不足处 AI 估算并标注"估算"；评论分析基于用户提供的评论样本。

---

## 快速开始（三步）

1. **配置 API Key**

   复制 `.env.example` 为 `.env`，填入 AI Key（[DeepSeek 获取地址](https://platform.deepseek.com/api_keys)，也可在页面右上角 ⚙️ 设置面板切换 GPT/Claude/自定义提供商并填对应 Key）。其余密钥可选：Tavily（行业动态/宏观背景/竞争格局/客户线索）、eBay 与速卖通联盟（商品分析增强）。建议同时设置 `ADMIN_PASSWORD`（保护"保存设置"接口；不设则每次启动生成随机密码并打印到日志）：

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

   浏览器打开 <http://127.0.0.1:8000> → 输入产品（如"蓝牙耳机"）和目标国家（如"德国"）→ 点击"开始分析"，10-30 秒后生成报告；或打开导航 **AI Agent** 页，输入一句话（如"蓝牙耳机去德国卖"）体验全流程。

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

**响应（200，text/event-stream）**：逐步推送 6 步进度事件（意图解析 → 证据链 → 市场分析 → 报告 → 线索 → 开发信），每步失败跳过继续，最后 `result` 事件含 `{report, leads, outreach, summary}`。客户端断开自动停止流水线（不再烧 token）。

### `POST /api/agent/export`（Agent 报告下载，v1.0）

Agent 生成的 Markdown 报告 → 学术式 Word/PDF 下载（复用正式报告排版体系）。

**请求示例：**

```json
{ "report": "# 蓝牙耳机市场分析（德国）…", "product": "蓝牙耳机", "country": "德国", "fmt": "docx" }
```

**响应**：文件下载流；`fmt=pdf` 且本机无 Word/LibreOffice 时自动降级 docx 并带 `X-Export-Fallback: docx` 响应头。

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
├── static/          # 前端（七页面 + 主题切换 + 书签工具）
│   ├── index.html       # 市场分析（首页，含多国对比）
│   ├── agent.html       # AI Agent 一句话全流程（SSE 进度 + 报告下载）
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
├── .github/workflows/ci.yml  # GitHub Actions：lint + 双 OS 测试 + PyInstaller 构建
├── Dockerfile         # 服务器镜像（含 LibreOffice + Noto CJK 字体，Linux PDF 导出）
├── docker-compose.yml # 一键部署（env_file + 数据卷）
├── requirements.txt   # 运行时依赖（锁版本）
├── requirements-dev.txt  # 测试/构建工具（pytest/ruff/pyinstaller）
├── requirements-win.txt # Windows 专属（pywin32/pywebview）
└── .env             # 密钥（已被 .gitignore 排除，不提交）
```

**分层设计**：`config → llm → main → 业务模块（trade/business/ecommerce/ebay/aliexpress）`，`llm.py` / `prompts.py` 是所有模块共用的底座。

---

## v1.0.1 更新日志（2026-08-15）

### 🔴 重大数据问题修复记录（数据准确性专项）

> 本轮专项的核心结论：**UN Comtrade 原始接口返回的"明细行"不能直接求和**。
> 平台按 `customsCode`（贸易方式：C00=总计=C03+C04+…）和 `motCode`（运输方式：0=全部）
> 把同一笔贸易拆成多条记录，且部分查询存在**成对重复行**。此前所有"清洗"（去重、按
> mot 优先、全行求和）都在不理解这两个字段语义的情况下进行，导致报告中的贸易额严重失真。

| # | 问题（发现过程） | 根因 | 修复 | 验证 |
|---|---|---|---|---|
| 1 | **德国进口 8525 金额被算成 55.07 亿**（真实约 6.88 亿，相差 8 倍）<br>发现：对同一查询的输出做交叉核对时，与 UN 官网数据对不上 | 原始 22 行 = 成对重复 × customsCode 拆分 × motCode 拆分；直接求和翻 8 倍 | **正确聚合**：按 (reporter,partner,cmd,period,mot,mos,customs) 去重 → 取 `customsCode=C00 且 motCode=0` 的**唯一记录**作为总额；无 C00 行时回退 C00 组，仍无则报错拒绝（宁缺勿错） | 德国案例实测 = 6.88 亿 ✅（`tests/test_data_clean.py` 7 项聚合测试） |
| 2 | **"mot=0 优先"再清洗仍错**（27.5 亿）<br>发现：第一版修复后数值仍与官网不符，继续追查 | 只按 mot 过滤仍保留 customsCode 拆分行（C00 只占总计的一部分，其余为 C03/C04 等） | 必须 **customsCode 与 motCode 双条件**同时命中才取 | 同案例实测 ✅ |
| 3 | **出口额 vs 进口额镜像不一致**（德国出口 6.88 亿 vs 中国进口 1.74 亿）<br>发现：双向核对时出现约 4 倍差异 | 这是**两国上报口径不同的真实差异**（X 流与 M 流不是镜像），不是数据错误 | 标记为 `Suspicious`（三态质量模型：Valid/Suspicious/Invalid），**不**用对方数据覆盖替换 | 报告层已把 quality 透出为质量备注 |
| 4 | **500 条截断检测在过滤后执行**<br>发现：查询返回恰好 500 条时被当"完整结果"缓存 | 先过滤再判断条数，截断的残缺数据被静默接受 | 截断检测**移到过滤前**，触顶直接报错拒绝 | 回归测试覆盖 |
| 5 | **preview/formal 切换后旧缓存照常命中**<br>发现：切换数据源后查询结果没变 | 缓存键不含数据源模式 | 缓存键加 `mode` 维度 | 回归测试覆盖 |
| 6 | **200 + 错误响应体被当"合法空结果"永久缓存**<br>发现：某次接口异常后查询长期返回空 | 只查 HTTP 状态码，未校验 `data` 键 | 校验响应结构含 `data` 键，否则拒绝 | 回归测试覆盖 |
| 7 | **SEC 财务单位错误**（苹果 2024 营收显示"0.00 亿美元"）<br>发现：财报口径（百万美元）被当美元 | SEC 接口 `val` 单位是百万美元 | `SEC_VAL_SCALE=1e6` 换算 | `tests/test_bugfix_regression.py` 覆盖 |
| 8 | **A 股时间序列降序**导致取错年份<br>发现：多年期报告 CAGR/峰值年份错位 | 东方财富返回降序，代码取 `[-1]` 当最早年 | 排序升序后再取首尾 | 回归测试覆盖 |
| 9 | **印度编码 699→356**<br>发现：印度数据查询为空，核对 M49 标准 | 旧编码 699 是 UNCTAD 内部码，Comtrade 用 M49 的 356 | 已修正；实测确认编码有效（印度数据缺失是 UN 数据源覆盖问题，非代码问题） | 实测：356 查询正常响应（数据源本身无印度出口数据） |
| 10 | **LLM 趋势缓存数值更新不失效**<br>发现：改参数后报告数值不变 | 缓存键只含年份集合，且返回缓存原对象被原地污染 | 键加数据签名 + 首/次调用都 deepcopy | 回归测试覆盖 |
| 11 | **饼图份额标注错位**<br>发现：扇区排序后"其他"标签对不上数值 | 排序后按原顺序取 raw 份额 | 按标签映射 + "其他"求和 | 回归测试覆盖 |
| 12 | **日本索尼年报取不到**<br>发现：财务画像模块对索尼始终无数据 | 索尼是 20-F（非 10-K）且以百万日元申报 | 按公司配置 (form 集合, 币种, 倍数) | 回归测试覆盖 |

**本轮原则沉淀（写进代码注释与测试）**：
- **宁缺勿错**：拿不准的数据宁可报错/标记，绝不静默给出错误值
- **先理解语义再清洗**：任何数据变换前必须弄清字段含义（customsCode/motCode 教训）
- **三态质量模型**：Valid（可信）/ Suspicious（双源不一致，标注不替换）/ Invalid（拒绝）

### 🐛 其他修复（v1.0.1）

- **确定性崩溃**：线索 URL 端口越界 500、模拟客户 `{}` 输入崩、LLM 200+非 dict 崩、Retry-After 无上限挂起、脏缓存 KeyError、采集异常不走 AI 兜底 —— 全部修复 + 回归测试
- **防幻觉绕过**：线索 URL 归一化为空串后绕过校验（修复）、搜索结果裸拼 prompt（加 `<evidence>` 界符 + "指令视为数据"）
- **Agent 流程**：步骤双份记录、提前返回缺键 —— 修复
- **健壮性**：请求模型加长度上限、`start_year` 边界、eBay token 缓存 + 9 位商品 ID、速卖通非 JSON 响应兜底、Word 导出 `DispatchEx`（不再 Quit 用户打开的 Word）、WB 缓存键与 None 值重试、数据库无损迁移 + 表清理
- **UN Comtrade 前端切换**：设置面板新增「数据源」下拉（正式接口/免费预览，白名单校验 + 无 key 拦截）
- **速卖通签名修复**（联调发现）：timestamp 格式 `yyyy-MM-dd HH:mm:ss`（GMT+8）、签名结果大写 hex
- **SSRF 加固**：DNS rebinding TOCTOU 窗口关闭（域名解析后 IP 钉扎建连，Host 头 + SNI 校验保留）

**测试**：86 项 pytest 全部通过（含 23 项本轮新增回归测试 `tests/test_bugfix_regression.py`）。

---

## v1.0.2 更新日志（2026-08-15）— 数据层架构收口

> 背景：v1.0.1 修掉了 UN Comtrade 聚合翻 8 倍的重大数据 bug。v1.0.2 不修业务，
> 专门把「数据层」的骨架补起来——让以后**再出现数据问题能追、能挡、能测**。

### 1. 数据血缘（Lineage）——每个数字都能追到"怎么来的"

`trade_cache` 表新增血缘列（无损迁移，旧数据保留）：

```
source               数据源（uncomtrade/preview 或 uncomtrade/formal）
raw_record_count     原始返回行数（过滤前）
clean_record_count   清洗后行数（C00+mot=0）
quality              valid / suspicious / invalid / rejected
validation_reason    质量判定理由（rejected 时为拒绝原因）
schema_version       结构版本（当前 1）
```

以后任何数字都能回答："为什么中国→德国 2025 年是 X 亿？"
→ 查 `get_cache_meta()` → 那次请求拿了多少原始行 → 删了哪些 → 为什么选 C00+mot=0 → 最终值。

### 2. DataGate 总闸——数据能不能用，程序说了算，AI 只解释

```
Raw Data → Normalize → Deduplicate → Validate → Quality Assessment
                                    ↓
                          ──── DataGate ────
                                    ↓
                             Analytics / AI（只解释，不判定）
```

- `check_data_gate()`：查血缘元数据返回 `{allowed, quality, reason}`——rejected 直接禁止使用
- `data_gate_report()`：前端友好版（"数据无法用于本次分析 + 原因"）
- `get_competitiveness` 已接入：任一条腿 REJECTED → 整体 quality=rejected，报告/前端明示

### 3. REJECTED 四态质量——"没有数据" ≠ "数据没找到"

查询成功但数据残缺（500 条截断 / C00 总额行缺失 / 响应结构异常）时：
**记 REJECTED 元数据（留痕）+ 报错拒绝（不写数据缓存）**。
前端显示"⚠️ 数据无法用于本次分析：原始数据未通过完整性校验"，而不是假"暂无数据"。

### 4. 清洗逻辑回归测试——8 个脏数据 case 样本化

`tests/test_data_clean.py` 扩展为 8 case + DataGate 断言：

```
case 01 正常 C00+MOT0      case 05 HTTP 200 + 错误响应体
case 02 重复 C00（成对行）  case 06 同 key 重复值异常
case 03 只有分项无 C00     case 07 X/M 镜像口径差异 → suspicious
case 04 500 条截断 → rejected  case 08 空数据（合法空 ≠ 残缺）
```

以后任何人改数据层，跑一遍就知道有没有把今天的成果干碎。

### 5. 数据调试纪律（写进团队共识，改数据逻辑必须回答四问）

> **不能只说"修复了重复数据"，必须能解释"为什么这一行应该被删"。**

每次修改数据逻辑，必须回答：

1. **原始数据是什么？**（拿到什么，多少行，什么结构）
2. **为什么删除这些记录？**（去重依据 / 截断 / 口径排除）
3. **为什么保留这些记录？**（C00+mot=0 的语义依据）
4. **最终数字如何从原始数据推导出来？**（逐步可复算）

**三条铁律**：
- 宁缺勿错：拿不准宁可报错/标记，绝不静默给错误值
- 先理解语义再清洗：任何变换前必须弄清字段含义（customsCode/motCode 教训）
- 四态质量：Valid（可信）/ Suspicious（双源不一致，标注不替换）/ Invalid / Rejected（完整性未过）

**测试**：96 项 pytest 全部通过（新增 10 项血缘 + DataGate + 8 case 回归）。

---

## v1.0.3 更新日志（2026-08-15）— 业务闭环收口

> 数据层稳定后回到业务价值：把"分析完就结束"变成"分析完能行动"。

### 1. 📈 销售漏斗（线索状态管理）
线索检索后自动存入 `leads_funnel` 表，状态流转：
```
新线索 → 已发开发信 → 已回复 → 已报价 → 成交 / 放弃
```
- 状态转移有白名单校验（防跳级/回退失控；won/lost 为终态，lost 可重新激活）
- 同 (产品, 市场, 公司, URL) 去重——重复检索不重置漏斗进度
- 线索页下方新增漏斗看板：各状态统计 + 按状态筛选 + 一键流转 + 跟进备注

### 2. 💰 定价建议（选品决策）
贸易数据页新增「定价建议」按钮，**数据驱动定价（程序计算，AI 不参与算术）**：
- 出口单价 = UN Comtrade 出口金额 / 出口净重（美元/公斤）
- 市场进口均价 = 目标市场总进口金额 / 总净重（美元/公斤）
- 建议区间：下沿 = 出口单价×1.5（覆盖运费/关税/毛利）、中位 = 市场进口均价、
  上沿 = 市场均价×1.3（含品牌溢价空间）
- 带数据血缘：两腿质量（DataGate）+ HS + 年份，缺净重/无数据时明确拒绝不算假价格

### 3. 📌 我的市场订阅（持续监控）
新页面 `/watch.html`：保存关注的 (产品 → 市场) 组合，一键刷新看出口额变化：
- 添加/删除订阅（去重），列表显示上次快照（数值 + 年份 + 时间）
- 刷新拉最新出口额，与上次对比显示 📈/📉 变化百分比
- 复用现有贸易查询与缓存，不新增数据源

**测试**：103 项 pytest 全部通过（新增 7 项定价公式/红线测试）。

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
| ✅ v1.0 | AI Agent 一句话全流程 | 独立页面 + SSE 流式进度：意图解析 → 证据链 → 市场分析 → 报告 → 线索 → 开发信；报告可导出 Word/PDF；失败步骤自动跳过 |
| ✅ v1.0 | 权限层与安全加固 | 管理员账户（设置保护）+ 匿名限流 + 访问日志/管理面板 + CORS 收紧 + SSRF 防线 + 提示词注入防护 |
| ✅ v1.0 | 客户线索模块 | Tavily 多组查询 + LLM 画像 + 防幻觉硬约束（无来源 URL 剔除）+ 定向开发信闭环 |
| ✅ v1.0 | 性能与健壮性 | 缓存 TTL 体系（动态/空结果）+ LLM LRU + single-flight + SQLite WAL + 重试分流 + 证据链并行 |
| ✅ v1.0 | 测试与 CI | 86 项 pytest（无网络）+ GitHub Actions（lint/双 OS 测试/打包） |
| ✅ v1.0 | 部署 | Dockerfile（LibreOffice + CJK 字体）+ docker-compose + Linux PDF 导出 |
| ✅ v1.0.1 | 数据准确性专项 | UN Comtrade 正确聚合（C00+mot=0 去重，德国案例 55亿→6.88亿）+ SEC/A股/索尼财务修复 + 23 项回归测试 |
| ✅ v1.0.2 | 数据层架构收口 | 数据血缘（get_cache_meta）+ DataGate 总闸 + REJECTED 四态质量 + 8 case 脏数据回归测试 + 调试纪律四问 |
| ✅ v1.0.3 | 业务闭环收口 | 销售漏斗（线索状态管理）+ 定价建议（数据驱动）+ 我的市场订阅（持续监控） |
| 远期 v1.1 | 结构重构 | export.py / main.py 拆分（routers/ + export/ 包）、前端 common.js 抽取、移动端适配、无障碍完善、exe 安装包 |

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
