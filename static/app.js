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

      // 渲染双栏仪表盘：KPI + 趋势图 + 证据链
      renderDash(data, product, country);

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

  // ===== 双栏仪表盘 =====
  let dashChart = null;

  function dashColors() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
      ? { axis: '#9aa8ba', line: '#2a3d55', accent: '#e06a4c', area: 'rgba(224,106,76,0.18)' }
      : { axis: '#7a715f', line: '#e3dccd', accent: '#c4452c', area: 'rgba(196,69,44,0.12)' };
  }

  function renderDash(data, product, country) {
    // 仪表盘渲染失败不应伪装成"网络错误"：数据与报告才是主交付物
    try {
      _renderDash(data, product, country);
    } catch (e) {
      console.warn('仪表盘渲染失败（不影响报告）:', e);
    }
  }

  function _renderDash(data, product, country) {
    const dashEl = document.getElementById('dash');
    const trade = data.trade || {};
    const comp = data.competitiveness || {};
    const mc = data.market_context || {};
    const bg = data.background || {};
    let any = false;

    // KPI 1：中国对目标市场出口额（UN Comtrade 真实数据）
    const kpiTrade = document.getElementById('kpi-trade');
    const kpiTradeLbl = document.getElementById('kpi-trade-lbl');
    if (trade.trend) {
      const years = Object.keys(trade.trend).map(Number).sort((a, b) => a - b);
      const lastY = years[years.length - 1];
      const lastVal = trade.trend[String(lastY)];  // 单位：亿美元
      kpiTrade.textContent = '$' + lastVal.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + '亿';
      kpiTradeLbl.textContent = '中国→' + country + ' 出口额（' + lastY + '）';
      any = true;
    } else {
      kpiTrade.textContent = '—';
      kpiTradeLbl.textContent = '出口数据缺失';
    }

    // KPI 2：CAGR（程序精确计算，基于真实趋势数据）
    const kpiCagr = document.getElementById('kpi-cagr');
    if (trade.trend && Object.keys(trade.trend).length >= 2) {
      const years = Object.keys(trade.trend).map(Number).sort((a, b) => a - b);
      const firstV = trade.trend[String(years[0])];
      const lastV = trade.trend[String(years[years.length - 1])];
      const n = years.length - 1;
      if (firstV > 0 && lastV > 0 && n > 0) {
        const cagr = (Math.pow(lastV / firstV, 1 / n) - 1) * 100;
        kpiCagr.textContent = (cagr >= 0 ? '+' : '') + cagr.toFixed(1) + '%';
      } else {
        kpiCagr.textContent = '—';
      }
    } else {
      kpiCagr.textContent = '—';
    }

    // KPI 3：TC 竞争力指数
    const kpiTc = document.getElementById('kpi-tc');
    if (comp.tc != null) {
      kpiTc.textContent = comp.tc.toFixed(2);
      any = true;
    } else {
      kpiTc.textContent = '—';
    }

    // 趋势图（echarts 未加载时跳过，不影响其他内容）
    if (trade.trend && Object.keys(trade.trend).length && typeof echarts !== 'undefined') {
      const years = Object.keys(trade.trend).map(Number).sort((a, b) => a - b);
      const values = years.map(y => trade.trend[String(y)]);
      const c = dashColors();
      const chartEl = document.getElementById('trend-chart');
      if (dashChart) { dashChart.dispose(); }
      dashChart = echarts.init(chartEl);
      dashChart.setOption({
        backgroundColor: 'transparent',
        tooltip: {
          trigger: 'axis',
          formatter: p => p[0].name + '年：' + p[0].value.toLocaleString('zh-CN') + ' 亿美元',
        },
        grid: { left: 60, right: 16, top: 20, bottom: 32 },
        xAxis: { type: 'category', data: years, axisLabel: { color: c.axis } },
        yAxis: {
          type: 'value',
          axisLabel: { color: c.axis, formatter: v => v + '亿' },
          splitLine: { lineStyle: { color: c.line } },
        },
        series: [{
          name: '出口额',
          type: 'line',
          data: values,
          smooth: true,
          symbolSize: 7,
          lineStyle: { color: c.accent, width: 3 },
          itemStyle: { color: c.accent },
          areaStyle: { color: c.area },
        }],
      });
      document.getElementById('trend-title').textContent =
        '中国 → ' + country + ' 出口趋势图（' + years[0] + '–' + years[years.length - 1] + '）';
      any = true;
    } else {
      document.getElementById('trend-title').textContent = '出口趋势图（暂无数据）';
    }

    // 证据链清单
    const evidList = document.getElementById('evid-list');
    evidList.innerHTML = '';
    const items = [];
    if (trade.trend) {
      const years = Object.keys(trade.trend).map(Number).sort((a, b) => a - b);
      const lastY = years[years.length - 1];
      items.push({
        src: 'UN COMTRADE',
        text: '中国对' + country + '出口 HS' + (trade.hs_code || '') + '：' + lastY + ' 年 ' +
          trade.trend[String(lastY)] + ' 亿美元',
      });
    }
    if (mc.gdp_per_capita) {
      items.push({ src: 'WORLD BANK', text: country + '人均 GDP ' + Math.round(mc.gdp_per_capita).toLocaleString() + ' 美元' });
    } else if (comp.tc != null) {
      items.push({ src: 'WORLD BANK', text: '竞争力指数 TC ' + comp.tc.toFixed(2) + '（出口 ' +
        (comp.export_value / 1e8).toFixed(2) + ' 亿 vs 进口 ' + (comp.import_value / 1e8).toFixed(2) + ' 亿美元）' });
    }
    if (data.news && data.news.headlines && data.news.headlines.length) {
      const first = data.news.headlines[0].title;
      const more = data.news.headlines.length > 1 ? ' 等 ' + data.news.headlines.length + ' 条' : '';
      items.push({ src: 'TAVILY', text: '行业动态：' + first + more });
    }
    if (bg.summary) {
      items.push({ src: 'WTO', text: '宏观背景：' + bg.summary });
    } else if (bg.global_trade_growth) {
      items.push({ src: 'WTO', text: '宏观背景：全球贸易增长预测 ' + bg.global_trade_growth });
    }
    items.forEach(it => {
      const li = document.createElement('li');
      const src = document.createElement('span');
      src.className = 'src';
      src.textContent = it.src;
      const txt = document.createElement('span');
      txt.textContent = it.text;
      const ok = document.createElement('span');
      ok.className = 'ok';
      ok.textContent = '✓';
      li.appendChild(src); li.appendChild(txt); li.appendChild(ok);
      evidList.appendChild(li);
    });

    // 有任一真实数据才显示仪表盘
    const hasData = any || evidList.children.length > 0;
    dashEl.hidden = !hasData;
    if (hasData) {
      // 先显示再 resize 图表（hidden 容器 init 尺寸为 0）
      setTimeout(() => { if (dashChart) dashChart.resize(); }, 50);
    }
  }
})();
