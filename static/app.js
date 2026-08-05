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

        let filename = '报告.docx';
        const disposition = resp.headers.get('Content-Disposition');
        if (disposition && disposition.indexOf('filename*=UTF-8\'\'') !== -1) {
            filename = decodeURIComponent(disposition.split('filename*=UTF-8\'\'')[1]);
        }
        a.download = filename;
        a.click();
        URL.revokeObjectURL(a.href);
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
