/* TradePilot AI — app.js：表单提交 → 调用后端 → 渲染 Markdown 报告 */
(function () {
  'use strict';

  const form = document.getElementById('analyze-form');
  const btn = document.getElementById('submit-btn');
  const statusEl = document.getElementById('status');
  const reportEl = document.getElementById('report');
  const dlBtn = document.getElementById('dl-report');
  let lastQuery = null;

  dlBtn.addEventListener('click', async () => {
    if (!lastQuery) return;
    dlBtn.disabled = true;
    dlBtn.textContent = '生成中…';
    try {
      const resp = await fetch('/api/analyze/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(lastQuery),
      });
      if (resp.ok) {
        const blob = await resp.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        // 从响应头 Content-Disposition 提取精确文件名（RFC5987 解码）
        const cd = resp.headers.get('Content-Disposition') || '';
        const m = cd.match(/filename\*=UTF-8''([^;]+)/i) || cd.match(/filename="?([^";]+)"?/i);
        a.download = m ? decodeURIComponent(m[1]) : 'TradePilot-市场分析报告.docx';
        a.click();
      } else {
        showStatus('报告下载失败', 'error');
      }
    } catch (_) {
      showStatus('网络错误，下载失败', 'error');
    } finally {
      dlBtn.disabled = false;
      dlBtn.textContent = '下载 Word 报告';
    }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const product = document.getElementById('product').value.trim();
    const country = document.getElementById('country').value.trim();
    if (!product || !country) {
      showStatus('请填写产品和目标国家', 'error');
      return;
    }

    btn.disabled = true;
    btn.textContent = '分析中…';
    btn.classList.add('loading');
    dlBtn.hidden = true;
    showStatus('正在生成市场分析报告，约需 10-30 秒…', 'info');
    // 新请求开始即清空旧报告，避免误读上次结果
    reportEl.hidden = true;
    reportEl.innerHTML = '';

    try {
      const resp = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product, country }),
      });
      // 后端可能返回非 JSON（如 502 网关错误页），先防御再解析
      let data = {};
      try {
        data = await resp.json();
      } catch (_) {
        if (resp.ok) {
          showStatus('后端响应格式异常，请刷新重试', 'error');
          return;
        }
      }

      if (!resp.ok) {
        showStatus(data.detail || '请求失败，请稍后重试', 'error');
        return;
      }

      // DOMPurify 先过滤再插入，防止报告内容里的恶意 HTML
      reportEl.innerHTML = DOMPurify.sanitize(marked.parse(data.report));
      reportEl.hidden = false;
      lastQuery = { product, country };
      dlBtn.hidden = false;

      // 行业动态（Tavily 搜索）
      const newsBox = document.getElementById('news-box');
      const newsList = document.getElementById('news-list');
      const headlines = (data.news && data.news.headlines) || [];
      if (headlines.length) {
        newsList.innerHTML = '';
        headlines.forEach(h => {
          const li = document.createElement('li');
          const a = document.createElement('a');
          a.href = h.url || '#';
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          a.textContent = h.title || '';
          a.style.color = 'var(--accent)';
          li.appendChild(a);
          newsList.appendChild(li);
        });
        newsBox.hidden = false;
      } else {
        newsBox.hidden = true;
      }

      // 数据依据（真实贸易数据 + 竞争力指标）
      const evBox = document.getElementById('evidence-box');
      const evTrade = document.getElementById('evidence-trade');
      const evComp = document.getElementById('evidence-comp');
      const trade = data.trade || {};
      const comp = data.competitiveness || {};
      let hasEvidence = false;
      if (trade.trend) {
        const years = Object.keys(trade.trend).sort();
        const trendStr = years.map(y => y + '年 ' + trade.trend[y] + ' 亿美元').join('，');
        evTrade.textContent = '贸易数据（UN Comtrade）: HS' + trade.hs_code + ' 出口额 ' + trendStr;
        hasEvidence = true;
      } else {
        evTrade.textContent = '';
      }
      if (comp.tc != null) {
        evComp.textContent = '竞争力指标: TC = ' + comp.tc + '（出口 ' +
          (comp.export_value / 1e8).toFixed(2) + ' 亿 vs 进口 ' +
          (comp.import_value / 1e8).toFixed(2) + ' 亿美元）';
        hasEvidence = true;
      } else {
        evComp.textContent = '';
      }
      evBox.hidden = !hasEvidence;
      showStatus('', '');
    } catch (err) {
      showStatus('网络错误，请确认后端服务已启动', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '开始分析';
      btn.classList.remove('loading');
    }
  });

  function showStatus(text, type) {
    statusEl.textContent = text;
    statusEl.className = 'status ' + type;
  }
})();
