# TradePilot AI — 跨境贸易智能平台

> 面向消费电子出海的 **AI 市场分析与业务辅助平台**。覆盖四个业务模块：**市场分析**（产品+国家 → 结构化市场报告 + 多国横向对比，基于真实数据证据链）、**贸易数据**（HS 编码 → 出口数据 + 趋势图 + AI 解读 + 竞争力指标）、**外贸业务**（英文开发信 / 跟进邮件 / 产品介绍 / AI 模拟客户）、**跨境电商**（商品采集 / 评论分析 / 竞品对比 / 平台 Listing / 财报画像 / eBay + 速卖通商品分析）。

**技术栈**：Python · FastAPI · SQLite · 原生 HTML/JS · ECharts · 多 AI 提供商（DeepSeek 默认 / GPT / Claude / 自定义）· UN Comtrade API · World Bank API · Tavily API · eBay Browse API · AliExpress 联盟开放平台 API

> ⚠️ **数据声明**：市场分析基于**多重真实数据证据链**（UN Comtrade 贸易数据 / World Bank 经济环境 / Tavily 行业动态 / WTO 宏观背景 / TC 竞争力指数），AI 引用并解读这些数据；统计指标（CAGR/峰值/竞争力）由程序精确计算，AI 不参与算数；数据不足处 AI 估算并标注"估算"；评论分析基于用户提供的评论样本。

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

---

## 目录结构

```
├── main.py          # FastAPI 入口：路由 + 报告渲染（含 null 兜底）
├── config.py        # 读取 .env 配置（AI/搜索/eBay/速卖通密钥，运行时热更新）
├── llm.py           # 多 AI 提供商调用层（DeepSeek/GPT/Claude/自定义）：重试、JSON 解析、证据链注入、缓存
├── prompts.py       # 系统提示词（9 字段 JSON 协议，IDC/学术报告风格）
├── trade.py         # 贸易数据模块：UN Comtrade 查询、组织聚合、统计指标、HS 编码 AI 自动解析
├── business.py      # 外贸业务模块：开发信 / 跟进 / 产品介绍 / 模拟客户
├── ecommerce.py     # 跨境电商模块：评论分析 / 商品画像分析 / 竞品对比 / Listing
├── collectors.py    # 商品数据采集层：URL 抓取 / JSON-LD / 粘贴 AI 提取（无 Key）
├── financials.py    # 财务画像：SEC 美股 / 东方财富 A 股 / 非上市白名单兜底
├── ebay.py          # eBay 商品分析（OAuth + Browse API，可选增强）
├── aliexpress.py    # 速卖通商品分析（联盟开放平台 API + HmacSHA256 签名）
├── export.py        # 报告导出：Word（docxtpl 模板）+ CSV 原始数据
├── database.py      # SQLite 缓存层（trade_cache / query_log）
├── market_data.py   # 多数据源：World Bank 经济 + Tavily 搜索 + WTO 宏观背景 + 竞争格局
├── desktop.py       # 桌面版入口（PyWebView）
├── countries.py     # 完整国家清单（159 项 + 组织代码）
├── hs_descriptions.py # HS 编码品名描述
├── data/
│   ├── sample_products.json  # 商品采集演示画像
│   ├── sample_reviews.json   # 旧演示评论数据
│   └── samples/              # 18 品类真实评论样本库（McAuley 公开数据集）
├── templates/
│   └── report_template_v2.docx  # Word 报告模板
├── scripts/         # 工具脚本（样本构建/清洗，不打包发行版）
├── static/          # 前端（四个页面 + 主题切换 + 书签工具）
│   ├── index.html       # 市场分析（含多国对比高级选项）
│   ├── trade.html       # 贸易数据
│   ├── business.html    # 开发信
│   ├── ecommerce.html   # 跨境电商
│   ├── app.js           # 市场分析页逻辑（单国/多国分流）
│   ├── bookmarklet.js   # 一键复制评论书签工具
│   ├── theme.js         # 双主题切换（☀️/🌙）
│   ├── settings.js      # 设置面板（API Key / AI 提供商 / 搜索源配置）
│   └── style.css        # 设计系统（亮色/暗色）
├── requirements.txt
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
