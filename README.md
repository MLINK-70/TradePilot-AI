# TradePilot AI — 跨境贸易智能平台

> 面向消费电子出海的 **AI 市场分析与业务辅助平台**。覆盖四个业务模块：**市场分析**（产品+国家 → 结构化市场报告）、**贸易数据**（HS 编码 → 出口数据 + 趋势图 + AI 解读）、**外贸业务**（英文开发信 / 跟进邮件 / 产品介绍 / AI 模拟客户）、**跨境电商**（评论分析 / 竞品对比 / 平台 Listing 生成）。

**技术栈**：Python · FastAPI · SQLite · 原生 HTML/JS · ECharts · DeepSeek API · UN Comtrade API

> ⚠️ **数据声明**：市场分析报告中的市场规模等数据由大模型估算；贸易数据来自 UN Comtrade 官方 API（真实数据）；评论分析基于用户提供的评论样本。

---

## 快速开始（三步）

1. **配置 API Key**

   复制 `.env.example` 为 `.env`，填入 DeepSeek API Key（[获取地址](https://platform.deepseek.com/api_keys)）：

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

---

## 目录结构

```
├── main.py          # FastAPI 入口：路由 + 报告渲染（含 null 兜底）
├── config.py        # 读取 .env 配置（DeepSeek / eBay 密钥）
├── llm.py           # DeepSeek 调用层：直连、重试、JSON 解析、AI 解读
├── prompts.py       # 系统提示词（6 字段 JSON 协议，产品核心价值）
├── trade.py         # 贸易数据模块：UN Comtrade 查询、组织聚合、统计指标
├── business.py      # 外贸业务模块：开发信 / 跟进 / 产品介绍 / 模拟客户
├── ecommerce.py     # 跨境电商模块：评论分析 / 竞品对比 / Listing
├── ebay.py          # eBay 商品分析（OAuth + Browse API）
├── export.py        # 报告导出：Word（docxtpl 模板）+ CSV 原始数据
├── database.py      # SQLite 缓存层（trade_cache / query_log）
├── countries.py     # 完整国家清单（159 项 + 组织代码）
├── hs_descriptions.py # HS 编码品名描述
├── data/
│   └── sample_reviews.json  # 演示评论数据
├── templates/
│   └── report_template_v2.docx  # Word 报告模板
├── static/          # 前端（四个页面 + 主题切换 + 书签工具）
│   ├── index.html       # 市场分析
│   ├── trade.html       # 贸易数据
│   ├── business.html    # 开发信
│   ├── ecommerce.html   # 跨境电商
│   ├── bookmarklet.js   # 一键复制评论书签工具
│   ├── theme.js         # 双主题切换（☀️/🌙）
│   └── style.css        # 设计系统（亮色/暗色）
├── requirements.txt
└── .env             # 密钥（已被 .gitignore 排除，不提交）
```

**分层设计**：`config → llm → main → 业务模块（trade/business/ecommerce/ebay）`，`llm.py` / `prompts.py` 是所有模块共用的底座。

---

## 路线图

| 阶段 | 模块 | 内容 |
| --- | --- | --- |
| ✅ 已完成 | 市场分析 Research | 产品 + 国家 → 结构化市场报告（6 字段） |
| ✅ 已完成 | 贸易数据 Trade | HS 编码 → 中国出口数据 + 趋势图 + AI 解读 + Word/CSV 导出 |
| ✅ 已完成 | 外贸业务 Business | 英文开发信（含真实数据引用）/ 跟进邮件 / 产品介绍+FAQ / AI 模拟客户 |
| ✅ 已完成 | 跨境电商 E-commerce | 评论分析 → 痛点报告 + 竞品对比 + 平台 Listing + 一键书签 |
| 远期 | AI Agent | 一句话 → 自动完成 查市场→查竞品→生成报告→生成开发信 全流程 |

**演进方式**：新模块上线时新建 `routers/` 目录，`main.py` 退化为"组装 app + include_router"；AI 调用底座（`llm.py` / `prompts.py`）保持不变。

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
