/* theme.js — 主题切换：默认跟随系统，点击按钮强制切换并记住选择 */
(function () {
  'use strict';

  const KEY = 'tradepilot-theme';  // 'dark' | 'light' | null(跟随系统)
  const btn = document.getElementById('theme-toggle');

  function apply(theme) {
    // theme: null=跟随系统, 'dark'=深色, 'light'=浅色
    if (theme === 'dark' || theme === 'light') {
      document.documentElement.setAttribute('data-theme', theme);
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
    updateIcon(theme);
  }

  function updateIcon(theme) {
    if (!btn) return;
    const isDark = theme
      ? theme === 'dark'
      : window.matchMedia('(prefers-color-scheme: dark)').matches;
    btn.textContent = isDark ? '☀️' : '🌙';
  }

  // 初始：读 localStorage 决定跟随系统还是手动主题
  const saved = localStorage.getItem(KEY);
  apply(saved);

  btn.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme');
    let next;
    if (!current) {
      // 跟随系统 → 取系统当前值作为手动起点
      next = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'light' : 'dark';
    } else {
      next = current === 'dark' ? 'light' : 'dark';
    }
    localStorage.setItem(KEY, next);
    apply(next);
  });

  // 跟随系统时，系统主题变化自动响应
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (!localStorage.getItem(KEY)) {
      apply(null);
    }
  });
})();
