/* TradePilot AI — app.js：表单提交 → 调用后端 → 渲染 Markdown 报告 */
(function () {
  'use strict';

  const form = document.getElementById('analyze-form');
  const btn = document.getElementById('submit-btn');
  const statusEl = document.getElementById('status');
  const reportEl = document.getElementById('report');
  const dlBtn = document.getElementById('dl-report');
  const dlPdfBtn = document.getElementById('dl-report-pdf');
  let lastQuery = null;

  // 通用下载：fmt=docx/pdf，按钮临时禁用 + 状态提示
  async function downloadReport(fmt, btnEl, btnText) {
    if (!lastQuery) return;
    btnEl.disabled = true;
    btnEl.textContent = '生成中…';
    try {
      const resp = await fetch('/api/analyze/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...lastQuery, fmt }),
      });
      if (resp.ok) {
        const blob = await resp.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        // 从响应头 Content-Disposition 提取精确文件名（RFC5987 解码）
        const cd = resp.headers.get('Content-Disposition') || '';
        const m = cd.match(/filename\*=UTF-8''([^;]+)/i) || cd.match(/filename="?([^";]+)"?/i);
        a.download = m ? decodeURIComponent(m[1]) : 'TradePilot-市场分析报告.' + fmt;
        a.click();
      } else {
        showStatus('报告下载失败', 'error');
      }
    } catch (_) {
      showStatus('网络错误，下载失败', 'error');
    } finally {
      btnEl.disabled = false;
      btnEl.textContent = btnText;
    }
  }

  dlBtn.addEventListener('click', () => downloadReport('docx', dlBtn, '下载 Word 报告'));
  dlPdfBtn.addEventListener('click', () => downloadReport('pdf', dlPdfBtn, '下载 PDF'));

  // ===== 历史记录（最近 10 条，点击回填重新查询）=====
  const historyBtn = document.getElementById('history-btn');
  const historyPanel = document.getElementById('history-panel');
  const historyList = document.getElementById('history-list');

  historyBtn.addEventListener('click', async () => {
    try {
      const resp = await fetch('/api/history');
      const items = await resp.json();
      historyList.innerHTML = '';
      if (!items.length) {
        const li = document.createElement('li');
        li.textContent = '暂无查询记录';
        li.style.cursor = 'default';
        historyList.appendChild(li);
      } else {
        items.forEach(it => {
          const li = document.createElement('li');
          const left = document.createElement('span');
          const typeTag = document.createElement('span');
          typeTag.className = 'h-type';
          typeTag.textContent = it.report_type === 'trade' ? '贸易' : '市场';
          // 贸易记录带年份参数
          let params = '';
          try {
            const p = JSON.parse(it.params || '{}');
            if (p.start_year) params = ' ' + p.start_year + '-' + (p.end_year || '最新');
          } catch (_) {}
          left.textContent = it.product + ' → ' + it.country + params;
          const time = document.createElement('span');
          time.className = 'h-time';
          time.textContent = (it.created_at || '').slice(5, 16).replace('T', ' ');
          li.appendChild(left);
          li.appendChild(typeTag);
          li.appendChild(time);
          li.addEventListener('click', () => {
            // 回填表单：市场记录填产品/国家，贸易记录跳转贸易页
            if (it.report_type === 'trade') {
              window.location.href = '/trade.html?product=' + encodeURIComponent(it.product) +
                '&target=' + encodeURIComponent(it.country) + '&start=' + (JSON.parse(it.params || '{}').start_year || '') +
                '&reporter=' + encodeURIComponent(JSON.parse(it.params || '{}').reporter || '中国');
            } else {
              document.getElementById('product').value = it.product;
              document.getElementById('country').value = it.country;
              historyPanel.hidden = true;
              form.requestSubmit();
            }
          });
          historyList.appendChild(li);
        });
      }
      historyPanel.hidden = false;
    } catch (_) {
      showStatus('历史记录加载失败', 'error');
    }
  });

  document.getElementById('history-close').addEventListener('click', () => {
    historyPanel.hidden = true;
  });

  // 历史跳转回填（?product=&country= → 填表单自动查询）
  (function autoFromHistory() {
    const params = new URLSearchParams(window.location.search);
    const product = params.get('product');
    const country = params.get('country');
    if (!product || !country) return;
    document.getElementById('product').value = product;
    document.getElementById('country').value = country;
    history.replaceState({}, '', window.location.pathname);
    form.requestSubmit();
  })();

  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const product = document.getElementById('product').value.trim();
    const country = document.getElementById('country').value.trim();
    if (!product || !country) {
      showStatus('请填写产品和目标国家', 'error');
      return;
    }

    // 高级选项：多市场对比（开关打开时走对比流程）
    const multiMarketBtn = document.getElementById('multi-market');
    if (multiMarketBtn && multiMarketBtn.getAttribute('aria-checked') === 'true') {
      const extra = Array.from(document.querySelectorAll('.compare-country'))
        .map(i => i.value.trim()).filter(Boolean);
      runCompare(product, [country, ...extra]);
      return;
    }

    btn.disabled = true;
    btn.textContent = '分析中…';
    btn.classList.add('loading');
    dlBtn.hidden = true; dlPdfBtn.hidden = true;
    showStatus('正在生成市场分析报告，约需 10-30 秒…', 'info');
    // 新请求开始即清空旧报告，避免误读上次结果
    reportEl.hidden = true;
    reportEl.innerHTML = '';
    // 单国分析时隐藏旧的多市场对比结果（避免残留误导）
    compareResult.hidden = true;

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
      dlBtn.hidden = false; dlPdfBtn.hidden = false;

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
  // 窗口缩放时重绘图表（审查第六批顺手项；防重复注册）
  if (!window.__dashResizeBound) {
    window.__dashResizeBound = true;
    window.addEventListener('resize', () => { if (dashChart) dashChart.resize(); });
  }

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

  // ===== 多市场对比（高级选项） =====
  const compareResult = document.getElementById('compare-result');
  const multiMarketBtn = document.getElementById('multi-market');
  const addCountryBtn = document.getElementById('add-country');

  // 精致 toggle 开关：点击切换状态 + 展开国家输入区
  multiMarketBtn.addEventListener('click', () => {
    const on = multiMarketBtn.getAttribute('aria-checked') === 'true';
    multiMarketBtn.setAttribute('aria-checked', String(!on));
    document.getElementById('multi-countries').hidden = on;
    // 开关重新打开时恢复"添加"按钮（若上次已隐藏）
    if (!on) { addCountryBtn.hidden = false; }
  });

  // "+ 添加"动态加国家输入框（最多到第 5 个国家后隐藏按钮）
  addCountryBtn.addEventListener('click', () => {
    const wrap = document.getElementById('compare-countries');
    if (wrap.querySelectorAll('.compare-country').length >= 4) { addCountryBtn.hidden = true; return; }
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'compare-country';
    input.placeholder = '国家' + (wrap.querySelectorAll('.compare-country').length + 2);
    input.setAttribute('list', 'country-list');
    input.autocomplete = 'off';
    wrap.insertBefore(input, addCountryBtn);
    input.focus();
    if (wrap.querySelectorAll('.compare-country').length >= 4) { addCountryBtn.hidden = true; }
  });

  // UN Comtrade 统一美元计价，出口额单位固定为亿美元（$ 前缀是单位的一部分，不是货币换算）
  function fmtMoney(v) {
    return '$' + Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + '亿';
  }

  function fmtCagr(trend) {
    const years = Object.keys(trend).map(Number).sort((a, b) => a - b);
    if (years.length < 2) return '—';
    const first = trend[String(years[0])];
    const last = trend[String(years[years.length - 1])];
    if (!(first > 0 && last > 0)) return '—';
    const cagr = (Math.pow(last / first, 1 / (years.length - 1)) - 1) * 100;
    const range = years[0] + '-' + years[years.length - 1];
    return (cagr >= 0 ? '+' : '') + cagr.toFixed(1) + '%（' + range + '）';
  }

  async function runCompare(product, countries) {
    if (countries.length < 2) {
      showStatus('多市场对比至少需要 2 个国家（补充对比国家）', 'error');
      return;
    }

    btn.disabled = true;
    btn.textContent = '分析中…（每市场独立查询，约 20-60 秒）';
    btn.classList.add('loading');
    dlBtn.hidden = true; dlPdfBtn.hidden = true;
    showStatus('正在生成多市场对比报告，请稍候…', 'info');
    // 新请求开始即清空旧对比结果
    reportEl.hidden = true;
    reportEl.innerHTML = '';
    document.getElementById('dash').hidden = true;  // 隐藏单国仪表盘（避免与对比结果混显）
    compareResult.hidden = true;
    // 保留初始 DOM 结构（table 骨架等），仅清空动态内容区
    document.querySelector('#compare-table tbody').innerHTML = '';
    document.getElementById('compare-recommend').innerHTML = '';
    document.getElementById('compare-insights').innerHTML = '';
    document.getElementById('compare-risks').innerHTML = '';

    try {
      const resp = await fetch('/api/analyze/compare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product, countries }),
      });
      let data = {};
      try { data = await resp.json(); } catch (_) {}
      if (!resp.ok) {
        showStatus(data.detail || '对比失败，请稍后重试', 'error');
        return;
      }

      renderCompare(data, product);
      compareResult.scrollIntoView({ behavior: 'smooth', block: 'start' });
      showStatus('', '');
    } catch (err) {
      showStatus('网络错误，请确认后端服务已启动', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '开始分析';
      btn.classList.remove('loading');
    }
  }

  function renderCompare(data, product) {
    document.getElementById('compare-product-name').textContent = product;
    compareResult.hidden = false;

    // AI 总览
    const cmp = data.comparison || {};
    const overview = document.getElementById('compare-overview');
    overview.textContent = cmp.overview || '';

    // 对比表：数字列用程序计算的 per_country（AI 不参与算术），解读列用 AI market_table
    const tbody = document.querySelector('#compare-table tbody');
    tbody.innerHTML = '';
    const aiRows = (cmp.market_table || []).reduce((m, r) => { m[r.country] = r; return m; }, {});
    data.countries.forEach(c => {
      const ev = (data.per_country && data.per_country[c]) || {};
      const te = ev.trade_evidence || {};
      const comp = ev.competitiveness || {};
      const ai = aiRows[c] || {};
      const tr = document.createElement('tr');
      // 出口额规模（最新一年）
      let size = '—';
      if (te.trend) {
        const years = Object.keys(te.trend).map(Number).sort((a, b) => a - b);
        const y = years[years.length - 1];
        size = fmtMoney(te.trend[String(y)]) + '（' + y + '）';
      }
      // TC
      const tc = comp.tc != null ? comp.tc.toFixed(2) : '—';
      [[c, '国家'], [size, '规模'], [fmtCagr(te.trend || {}), '增速'], [tc, '竞争力'],
       [ai.opportunity || '—', '机会点'], [ai.risk || '—', '风险']].forEach(([v, label]) => {
        const td = document.createElement('td');
        td.textContent = v || '—';
        if (label === '国家') td.className = 'cname';
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    document.getElementById('compare-table-wrap').hidden = false;

    // 入选建议（优先级带颜色）
    const recBox = document.getElementById('compare-recommend');
    recBox.innerHTML = '';
    (cmp.recommendations || []).forEach(r => {
      const p = document.createElement('p');
      const prioMap = { '优先': 'prio-hi', '次选': 'prio-mid', '观察': 'prio-low' };
      const cls = prioMap[r.priority] || '';
      const span = document.createElement('span');
      span.textContent = '【' + (r.priority || '建议') + '】';
      if (cls) span.className = cls;
      p.appendChild(span);
      const text = document.createElement('span');
      text.textContent = ' ' + (r.market || '') + '：' + (r.rationale || '') +
        (r.strategy ? ' 策略：' + r.strategy : '');
      p.appendChild(text);
      recBox.appendChild(p);
    });

    // 关键洞察 + 跨市场风险
    const fillList = (id, items) => {
      const ul = document.getElementById(id);
      ul.innerHTML = '';
      (items || []).forEach(t => {
        const li = document.createElement('li');
        li.textContent = t;
        ul.appendChild(li);
      });
    };
    fillList('compare-insights', cmp.key_insights);
    fillList('compare-risks', cmp.risks);
  }
})();
