/* settings.js — 设置面板：右上角 ⚙️ 按钮 + 弹窗，配置 API Key */

(function () {
  'use strict';

  const HTML = `
  <div id="settings-mask" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:90;"></div>
  <div id="settings-panel" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:420px;max-width:90vw;max-height:85vh;overflow-y:auto;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;z-index:100;box-shadow:0 8px 32px rgba(0,0,0,.3);">
    <h3 style="margin-bottom:12px;color:var(--accent);">设置</h3>
    <div style="margin-bottom:10px;">
      <label for="set-ai-provider" style="font-size:.85rem;color:var(--muted);">AI 提供商 <span style="color:var(--error);">*必备</span></label>
      <select id="set-ai-provider" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
        <option value="deepseek">DeepSeek（推荐 · 默认）</option>
        <option value="gpt">OpenAI GPT</option>
        <option value="claude">Claude</option>
        <option value="custom">自定义（OpenAI 兼容）</option>
      </select>
      <div style="font-size:.75rem;color:var(--muted);margin-top:2px;">AI 分析底座：市场报告 / 贸易解读 / 评论分析 / 开发信 全靠它生成</div>
    </div>
    <details style="margin-bottom:10px;border:1px dashed var(--border);border-radius:8px;padding:8px 10px;">
      <summary style="font-size:.85rem;color:var(--muted);cursor:pointer;user-select:none;list-style:none;display:flex;align-items:center;gap:6px;">
        <span style="transition:transform .2s;display:inline-block;" id="set-ai-more-arrow">▸</span>
        AI 提供商详细配置 <span style="font-size:.68rem;color:var(--teal);background:rgba(13,148,136,.1);padding:1px 8px;border-radius:999px;">Key · 模型</span>
      </summary>
      <div style="margin-top:10px;border-top:1px dashed var(--border);padding-top:10px;">
        <div style="margin-bottom:10px;">
          <label for="set-ai-key" style="font-size:.85rem;color:var(--muted);">API Key <span style="color:var(--error);">*必备</span></label>
          <input type="password" id="set-ai-key" placeholder="sk-..." style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
          <div style="font-size:.75rem;color:var(--muted);margin-top:2px;">DeepSeek: <a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener" style="color:var(--accent);">获取</a></div>
        </div>
        <div style="margin-bottom:10px;">
          <label for="set-ai-model" style="font-size:.85rem;color:var(--muted);">模型（可选，留空用默认）</label>
          <input type="text" id="set-ai-model" placeholder="如 deepseek-chat / gpt-4o-mini" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
        </div>
        <div style="margin-bottom:10px;">
          <label for="set-ai-base" style="font-size:.85rem;color:var(--muted);">Base URL（仅自定义需要）</label>
          <input type="text" id="set-ai-base" placeholder="https://api.openai.com/v1" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
        </div>
      </div>
    </details>
    <details style="margin-bottom:10px;border:1px dashed var(--border);border-radius:8px;padding:8px 10px;">
      <summary style="font-size:.85rem;color:var(--muted);cursor:pointer;user-select:none;list-style:none;display:flex;align-items:center;gap:6px;">
        <span style="transition:transform .2s;display:inline-block;" id="set-tavily-more-arrow">▸</span>
        搜索与行业数据 <span style="font-size:.68rem;color:var(--teal);background:rgba(13,148,136,.1);padding:1px 8px;border-radius:999px;">推荐 Tavily</span>
      </summary>
      <div style="margin-top:10px;border-top:1px dashed var(--border);padding-top:10px;">
        <label for="set-tavily" style="font-size:.85rem;color:var(--muted);">Tavily API Key（行业动态）</label>
        <input type="password" id="set-tavily" placeholder="tvly-..." style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
        <div style="font-size:.75rem;color:var(--muted);margin-top:2px;">
          行业动态 / 宏观背景 / 竞争格局的来源。未配置时这些板块缺失，其余功能不受影响。<a href="https://app.tavily.com" target="_blank" rel="noopener" style="color:var(--accent);">获取</a>
        </div>
      </div>
    </details>
    <details style="margin-bottom:14px;border:1px dashed var(--border);border-radius:8px;padding:8px 10px;">
      <summary style="font-size:.85rem;color:var(--muted);cursor:pointer;user-select:none;list-style:none;display:flex;align-items:center;gap:6px;">
        <span style="transition:transform .2s;display:inline-block;" id="set-more-arrow">▸</span>
        更多 API <span style="font-size:.68rem;color:var(--navy);background:var(--accent-soft);padding:1px 8px;border-radius:999px;">eBay · 速卖通</span>
      </summary>
      <div style="margin-top:10px;border-top:1px dashed var(--border);padding-top:10px;">
        <div style="margin-bottom:10px;">
          <label for="set-ebay-id" style="font-size:.85rem;color:var(--muted);">eBay 凭证（商品分析）</label>
          <input type="password" id="set-ebay-id" placeholder="App ID" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
          <input type="password" id="set-ebay-secret" placeholder="Client Secret" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
          <div style="font-size:.75rem;color:var(--muted);margin-top:2px;">可选 · eBay 商品分析（链接→价格/评分/卖家）。需 eBay 开发者审核 · 需梯子</div>
        </div>
        <div style="margin-bottom:10px;">
          <label for="set-aliexpress-key" style="font-size:.85rem;color:var(--muted);">速卖通联盟凭证（商品分析）</label>
          <input type="password" id="set-aliexpress-key" placeholder="App Key" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
          <input type="password" id="set-aliexpress-secret" placeholder="App Secret" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--card-2);color:var(--text);margin-top:4px;">
          <div style="font-size:.75rem;color:var(--muted);margin-top:2px;">可选 · 速卖通商品分析（链接→商品/价格/销量）。联盟开放平台 · 国内直连</div>
        </div>
      </div>
    </details>
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

  // 折叠区箭头旋转（AI 详细配置 / 搜索与行业数据 / 更多 API）
  panel.querySelectorAll('details').forEach(d => {
    const arrowId = d.querySelector('summary span');
    d.addEventListener('toggle', () => {
      if (arrowId) arrowId.style.transform = d.open ? 'rotate(90deg)' : '';
    });
  });

  // 右上角 ⚙️ 按钮（插入品牌栏右侧，与主题按钮并排）
  const gear = document.createElement('button');
  gear.textContent = '⚙️';
  gear.className = 'theme-toggle';
  gear.title = '设置';
  gear.setAttribute('aria-label', '设置');
  gear.addEventListener('click', open);
  const brandRight = document.querySelector('.brand-right');
  if (brandRight) {
    brandRight.appendChild(gear);
  } else {
    document.body.appendChild(gear);
  }

  async function open() {
    try {
      const resp = await fetch('/api/settings');
      const s = await resp.json();
      document.getElementById('set-ai-key').value = '';
      document.getElementById('set-ai-model').value = '';
      document.getElementById('set-ai-base').value = '';
      document.getElementById('set-tavily').value = '';
      document.getElementById('set-ebay-id').value = '';
      document.getElementById('set-ebay-secret').value = '';
      document.getElementById('set-aliexpress-key').value = '';
      document.getElementById('set-aliexpress-secret').value = '';
      // 显示已配置状态（placeholder 提示）
      const providerSel = document.getElementById('set-ai-provider');
      if (s.AI_PROVIDER && ['deepseek','gpt','claude','custom'].includes(s.AI_PROVIDER)) {
        providerSel.value = s.AI_PROVIDER;
      }
      if (s.DEEPSEEK_API_KEY || s.AI_API_KEY) document.getElementById('set-ai-key').placeholder = '已配置（留空保持不变）';
      if (s.AI_MODEL) document.getElementById('set-ai-model').placeholder = '当前：' + s.AI_MODEL;
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
    const provider = document.getElementById('set-ai-provider').value;
    const payload = {
      deepseek_key: document.getElementById('set-ai-key').value.trim(),
      tavily_key: document.getElementById('set-tavily').value.trim(),
      ebay_app_id: document.getElementById('set-ebay-id').value.trim(),
      ebay_client_secret: document.getElementById('set-ebay-secret').value.trim(),
      aliexpress_app_key: document.getElementById('set-aliexpress-key').value.trim(),
      aliexpress_app_secret: document.getElementById('set-aliexpress-secret').value.trim(),
      ai_provider: provider,
      ai_model: document.getElementById('set-ai-model').value.trim(),
      ai_base_url: document.getElementById('set-ai-base').value.trim(),
    };
    if (!payload.deepseek_key && !payload.tavily_key && !payload.ebay_app_id && !payload.ebay_client_secret && !payload.aliexpress_app_key && !payload.aliexpress_app_secret && !payload.ai_model && !payload.ai_base_url) {
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
