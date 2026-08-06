/* TradePilot 评论提取工具（bookmarklet）
 *
 * 用法：在任意电商商品页点击书签，自动提取当前页面评论，
 * 直接发送到本地 TradePilot 分析，新标签页打开结果。
 *
 * 合规说明：只提取页面当前已显示的内容（用户可见的），
 * 不翻页、不自动抓取、不突破限制。
 *
 * 前提：本地 TradePilot 服务已启动（http://127.0.0.1:8000）
 */
(function () {
  'use strict';

  const TP_BASE = 'http://127.0.0.1:8000';

  // 1. 扫描页面文本，找可能的评论块
  function extract() {
    const results = [];
    const seen = new Set();

    // 策略 A：评分图标（★/☆）附近的文本块
    const stars = document.querySelectorAll('[class*="star"], [class*="rating"], [class*="review"]');
    stars.forEach(function (el) {
      const container = el.closest('div') || el.parentElement;
      if (!container) return;
      const text = container.innerText || '';
      const lines = text.split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
      lines.forEach(function (line) {
        if (line.length >= 8 && line.length <= 300 && !seen.has(line) && !isNoise(line)) {
          seen.add(line);
          results.push(line);
        }
      });
    });

    // 策略 B：常见评论容器选择器
    const selectors = [
      '[data-hook="review"]', '.review-content', '.review-text',
      '[class*="review-content"]', '[class*="review-text"]',
      '[class*="comment-content"]', '[class*="comment-text"]',
      '.c-r-l', '.comment-item', '.review-item'
    ];
    selectors.forEach(function (sel) {
      document.querySelectorAll(sel).forEach(function (el) {
        const text = (el.innerText || '').trim();
        if (text.length >= 8 && text.length <= 500 && !seen.has(text) && !isNoise(text)) {
          seen.add(text);
          results.push(text);
        }
      });
    });

    return results;
  }

  // 2. 噪音过滤
  function isNoise(line) {
    const noiseWords = ['查看全部', '查看更多', '展开', '回复', '赞', '分享', '收藏',
      '购买', '加入购物车', '立即购买', '客服', '登录', '注册', '下一页', '上一页',
      'related', 'Sponsored', 'More', 'See more', 'Helpful', 'Report'];
    for (let i = 0; i < noiseWords.length; i++) {
      if (line === noiseWords[i] || line.indexOf(noiseWords[i]) === 0) return true;
    }
    return false;
  }

  // 3. 提取 + 直连分析
  const reviews = extract();
  if (!reviews.length) {
    alert('未识别到评论。\n请确认页面已滚动到评论区域，或手动复制评论后粘贴到 TradePilot。');
    return;
  }

  showToast('已提取 ' + reviews.length + ' 条评论，正在发送到 TradePilot 分析…');

  fetch(TP_BASE + '/api/ecommerce/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reviews: reviews }),
  })
    .then(function (resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.json();
    })
    .then(function (data) {
      // 把分析结果存到 localStorage，结果页读取
      localStorage.setItem('tp_last_analysis', JSON.stringify({
        reviews: reviews,
        analysis: data,
        source: document.title,
        url: location.href,
      }));
      window.open(TP_BASE + '/ecommerce.html#analysis', '_blank');
    })
    .catch(function (err) {
      alert('发送失败: ' + err.message + '\n请确认 TradePilot 服务已启动（127.0.0.1:8000）');
    });

  // 4. 提示浮层
  function showToast(msg) {
    const t = document.createElement('div');
    t.textContent = msg;
    t.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:99999;background:#2e5bff;color:#fff;padding:10px 20px;border-radius:8px;font-family:sans-serif;font-size:14px;box-shadow:0 4px 16px rgba(0,0,0,.3);';
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 3000);
  }
})();
