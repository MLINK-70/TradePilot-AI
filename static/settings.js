/* settings.js — 设置面板：右上角 ⚙️ 按钮 + 弹窗，配置 API Key */

(function () {
  'use strict';

  const HTML = `
  <div id="settings-mask" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:90;"></div>
  <div id="settings-panel" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:420px;max-width:90vw;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;z-index:100;box-shadow:0 8px 32px rgba(0,0,0,.3);">
    <h3 style="margin-bottom:12px;color:var(--accent);">设置</h3>
    <div style="margin-bottom:10px;">
      <label style="font-size:.85rem;color:var(--muted);">DeepSeek API Key <span style="color:var(--error);">*必填</span></label>
      <input type="password" id="set-deepseek" placeholder="sk-..." style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
      <div style="font-size:.75rem;color:var(--muted);margin-top:2px;">分析必用 · <a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener" style="color:var(--accent);">获取</a></div>
    </div>
    <div style="margin-bottom:10px;">
      <label style="font-size:.85rem;color:var(--muted);">Tavily API Key（行业动态）</label>
      <input type="password" id="set-tavily" placeholder="tvly-..." style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
      <div style="font-size:.75rem;color:var(--muted);margin-top:2px;">可选 · 新闻搜索 · <a href="https://app.tavily.com" target="_blank" rel="noopener" style="color:var(--accent);">获取</a></div>
    </div>
    <div style="margin-bottom:14px;">
      <label style="font-size:.85rem;color:var(--muted);">eBay 凭证（商品分析）</label>
      <input type="password" id="set-ebay-id" placeholder="App ID" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
      <input type="password" id="set-ebay-secret" placeholder="Client Secret" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
      <div style="font-size:.75rem;color:var(--muted);margin-top:2px;">可选 · 需 eBay 开发者审核 · 需梯子</div>
    </div>
    <div style="margin-bottom:14px;">
      <label style="font-size:.85rem;color:var(--muted);">速卖通联盟凭证（商品分析）</label>
      <input type="password" id="set-aliexpress-key" placeholder="App Key" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
      <input type="password" id="set-aliexpress-secret" placeholder="App Secret" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
      <div style="font-size:.75rem;color:var(--muted);margin-top:2px;">可选 · 速卖通联盟开放平台 · 国内直连</div>
    </div>
    <div style="display:flex;gap:8px;">
      <button id="set-save" style="flex:1;padding:8px;border:none;border-radius:6px;background:var(--accent);color:#fff;cursor:pointer;">保存</button>
      <button id="set-close" style="flex:1;padding:8px;border:1px solid var(--border);border-radius:6px;background:transparent;color:var(--muted);cursor:pointer;">关闭</button>
    </div>
    <p id="set-status" style="font-size:.8rem;margin-top:8px;color:var(--error);"></p>
  </div>`;

  document.body.insertAdjacentHTML('beforeend', HTML);
  const mask = document.getElementById('settings-mask');
  const panel = document.getElementById('settings-panel');
  const status = document.getElementById('set-status');

  // 右上角 ⚙️ 按钮（主题切换按钮旁边）
  const gear = document.createElement('button');
  gear.textContent = '⚙️';
  gear.className = 'theme-toggle';
  gear.style.right = '4.5rem';
  gear.title = '设置';
  gear.setAttribute('aria-label', '设置');
  gear.addEventListener('click', open);
  document.body.appendChild(gear);

  async function open() {
    try {
      const resp = await fetch('/api/settings');
      const s = await resp.json();
      document.getElementById('set-deepseek').value = '';
      document.getElementById('set-tavily').value = '';
      document.getElementById('set-ebay-id').value = '';
      document.getElementById('set-ebay-secret').value = '';
      document.getElementById('set-aliexpress-key').value = '';
      document.getElementById('set-aliexpress-secret').value = '';
      // 显示已配置状态（placeholder 提示）
      if (s.DEEPSEEK_API_KEY) document.getElementById('set-deepseek').placeholder = '已配置（留空保持不变）';
      if (s.TAVILY_API_KEY) document.getElementById('set-tavily').placeholder = '已配置（留空保持不变）';
      if (s.EBAY_APP_ID) document.getElementById('set-ebay-id').placeholder = '已配置（留空保持不变）';
      if (s.ALIEXPRESS_APP_KEY && s.ALIEXPRESS_APP_SECRET) {
        document.getElementById('set-aliexpress-key').placeholder = '已配置（留空保持不变）';
        document.getElementById('set-aliexpress-secret').placeholder = '已配置（留空保持不变）';
      } else if (s.ALIEXPRESS_APP_KEY || s.ALIEXPRESS_APP_SECRET) {
        document.getElementById('set-aliexpress-key').placeholder = '只填了一半（需 Key + Secret）';
        document.getElementById('set-aliexpress-secret').placeholder = '只填了一半（需 Key + Secret）';
      }
      // 提示：未配置 Tavily 时哪些功能不可用
      if (!s.TAVILY_API_KEY) {
        status.style.color = 'var(--gold)';
        status.textContent = '未配置 Tavily：行业动态/宏观背景/竞争格局将不可用';
      } else {
        status.textContent = '';
      }
    } catch (_) {}
    status.textContent = '';
    mask.style.display = 'block';
    panel.style.display = 'block';
  }

  function close() {
    mask.style.display = 'none';
    panel.style.display = 'none';
  }

  mask.addEventListener('click', close);
  document.getElementById('set-close').addEventListener('click', close);

  document.getElementById('set-save').addEventListener('click', async () => {
    const payload = {
      deepseek_key: document.getElementById('set-deepseek').value.trim(),
      tavily_key: document.getElementById('set-tavily').value.trim(),
      ebay_app_id: document.getElementById('set-ebay-id').value.trim(),
      ebay_client_secret: document.getElementById('set-ebay-secret').value.trim(),
      aliexpress_app_key: document.getElementById('set-aliexpress-key').value.trim(),
      aliexpress_app_secret: document.getElementById('set-aliexpress-secret').value.trim(),
    };
    if (!payload.deepseek_key && !payload.tavily_key && !payload.ebay_app_id && !payload.ebay_client_secret && !payload.aliexpress_app_key && !payload.aliexpress_app_secret) {
      status.textContent = '没有要保存的内容';
      return;
    }
    try {
      const resp = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const s = await resp.json();
      if (resp.ok) {
        status.style.color = 'var(--teal)';
        status.textContent = '已保存，立即生效';
        setTimeout(close, 1000);
      } else {
        status.style.color = 'var(--error)';
        status.textContent = '保存失败';
      }
    } catch (_) {
      status.textContent = '网络错误';
    }
  });
})();
