/* TradePilot 评论复制工具（bookmarklet）
 *
 * 用法：把下面的代码存成书签，在任意电商商品页点击，
 * 自动提取当前页面的评论文字并弹出可复制文本。
 *
 * 合规说明：只整理页面当前已显示的内容（用户可见的），
 * 不翻页、不自动抓取、不突破限制。
 */
(function () {
  'use strict';

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
        // 评论文本特征：长度 8-300 字符，不含按钮/导航词
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

  // 2. 噪音过滤（按钮/导航/广告词）
  function isNoise(line) {
    const noiseWords = ['查看全部', '查看更多', '展开', '回复', '赞', '分享', '收藏',
      '购买', '加入购物车', '立即购买', '客服', '登录', '注册', '下一页', '上一页',
      'related', 'Sponsored', 'More', 'See more', 'Helpful', 'Report'];
    for (let i = 0; i < noiseWords.length; i++) {
      if (line === noiseWords[i] || line.indexOf(noiseWords[i]) === 0) return true;
    }
    return false;
  }

  // 3. 弹窗展示
  const reviews = extract();
  if (!reviews.length) {
    alert('未识别到评论。\n请确认页面已滚动到评论区域，或手动复制评论后粘贴到 TradePilot。');
    return;
  }

  const text = reviews.join('\n');
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;top:10%;left:10%;width:80%;max-height:70%;z-index:99999;background:#fff;color:#111;padding:16px;border:2px solid #2e5bff;border-radius:8px;box-shadow:0 4px 24px rgba(0,0,0,.3);font-family:sans-serif;font-size:14px;';
  box.innerHTML =
    '<div style="font-weight:bold;margin-bottom:8px;">TradePilot 评论提取：识别到 ' + reviews.length + ' 条，已复制到剪贴板</div>' +
    '<textarea readonly style="width:100%;height:300px;box-sizing:border-box;padding:8px;font-size:12px;">' + text.replace(/</g, '&lt;') + '</textarea>' +
    '<div style="margin-top:8px;display:flex;gap:8px;">' +
    '<button id="tp-close" style="padding:6px 16px;border:none;background:#2e5bff;color:#fff;border-radius:4px;cursor:pointer;">关闭</button>' +
    '</div>';
  document.body.appendChild(box);

  // 复制到剪贴板
  function copy() {
    const ta = box.querySelector('textarea');
    ta.select();
    ta.setSelectionRange(0, 99999);
    try { document.execCommand('copy'); } catch (e) {}
  }
  copy();

  box.querySelector('#tp-close').addEventListener('click', function () { box.remove(); });
})();
