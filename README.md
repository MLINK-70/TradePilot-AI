# TradePilot AI — 跨境市场分析平台

> 面向消费电子出海的 AI 市场分析与业务辅助平台。输入产品名与目标国家，一键生成结构化市场调研报告（市场规模 / 增长趋势 / 热门品牌 / 用户画像 / 风险分析 / AI 总结）。

**技术栈**：Python · FastAPI · 原生 HTML/JS · DeepSeek API

> ⚠️ **数据声明**：报告中的市场数据由大模型估算生成，仅供参考，非官方统计数据。

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
├── main.py          # FastAPI 入口：路由 + Markdown 报告渲染（含 null 兜底）
├── config.py        # 读取 .env 配置（API Key / 模型）
├── llm.py           # DeepSeek 调用层：强制直连、自动重试、JSON 解析
├── prompts.py       # 系统提示词（6 字段 JSON 协议，产品核心价值）
├── static/          # 前端（原生 HTML/JS，marked.js 渲染 + CDN 降级容错）
│   ├── index.html
│   ├── app.js
│   └── style.css
├── requirements.txt
└── .env             # 密钥（已被 .gitignore 排除，不提交）
```

**分层设计**：`config → llm → main` 单向依赖，`llm.py` / `prompts.py` 是所有模块共用的底座，后续新模块复用，无需改动。

---

## 路线图

| 阶段 | 模块 | 内容 |
| --- | --- | --- |
| ✅ 第一版（已完成） | 市场分析 Research | 产品 + 国家 → 市场分析报告 |
| 第二版（进行中） | 贸易数据 Trade | HS 编码 / 产品名 → 中国出口情况 + 主要出口国 + 趋势图（接入真实数据源） |
| 第三版 | 外贸业务 Business | 开发信 / 跟进邮件 / 产品介绍 / 客户画像 |
| 第四版 | 跨境电商 E-commerce | Amazon 评论分析 → 用户痛点总结 |
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
