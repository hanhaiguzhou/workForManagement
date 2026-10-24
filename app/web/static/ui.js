// 值班排班系统 · UI v2 动效
// 1) 按钮按下涟漪  2) 课表格子错峰弹入  3) 导航高亮  4) 表单提交 loading

(() => {
  // ---- 北京时间助手 ----
  // 服务器可能是 UTC、用户设备也可能设成别的时区，所以凡是要判断
  // 「现在几点/今天星期几」的地方，一律换算到东八区再算。
  function beijingFormat(options) {
    try {
      return new Intl.DateTimeFormat('en-US',
        Object.assign({ timeZone: 'Asia/Shanghai' }, options)).format(new Date());
    } catch (e) {
      return null; // 老浏览器不认 timeZone
    }
  }
  function beijingWeekday() {
    const text = beijingFormat({ weekday: 'short' });
    const map = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 0 };
    if (text !== null && map[text] !== undefined) return map[text];
    return new Date().getDay();
  }

  // ---- 导航高亮 ----
  const path = location.pathname;
  document.querySelectorAll('.topbar nav a').forEach(a => {
    const href = a.getAttribute('href');
    if (!href) return;
    if (href === '/' ? path === '/' : path.startsWith(href)) a.classList.add('active');
  });

  // ---- 按钮涟漪 ----
  document.addEventListener('click', e => {
    const btn = e.target.closest('button, .button-link, .cta');
    if (!btn || btn.classList.contains('link')) return;
    const rect = btn.getBoundingClientRect();
    const ripple = document.createElement('span');
    const size = Math.max(rect.width, rect.height) * 2;
    // 白色涟漪在亮色主题的白底按钮上看不见，按点击那一刻的主题选色
    const light =
      (document.documentElement.getAttribute('data-theme') || 'dark') === 'light';
    ripple.style.cssText = `
      position:absolute;border-radius:50%;pointer-events:none;
      width:${size}px;height:${size}px;
      left:${e.clientX - rect.left - size / 2}px;top:${e.clientY - rect.top - size / 2}px;
      background:${light ? 'rgba(78,107,255,.18)' : 'rgba(255,255,255,.28)'};transform:scale(0);opacity:1;
      transition:transform .55s cubic-bezier(.22,1,.36,1),opacity .55s;
    `;
    btn.appendChild(ripple);
    requestAnimationFrame(() => {
      ripple.style.transform = 'scale(1)';
      ripple.style.opacity = '0';
    });
    setTimeout(() => ripple.remove(), 600);
  });

  // ---- 课表格子错峰弹入（按行列计算延迟） ----
  document.querySelectorAll('table.grid').forEach(table => {
    table.querySelectorAll('tbody tr').forEach((row, r) => {
      row.querySelectorAll('td.cell').forEach((cell, c) => {
        cell.style.animationDelay = `${0.02 + (r * 7 + c) * 0.014}s`;
      });
    });
  });

  // ---- 表格分页（data-page-size="10" 即每页 10 条） ----
  document.querySelectorAll('table[data-page-size]').forEach(table => {
    const size = parseInt(table.dataset.pageSize, 10) || 10;
    const body = table.tBodies[0];
    if (!body) return;
    const rows = Array.from(body.rows);
    const pageCount = Math.ceil(rows.length / size);
    if (pageCount <= 1) return;   // 一页装得下就不显示分页控件

    let page = 1;
    const pager = document.createElement('div');
    pager.className = 'pager';
    pager.innerHTML = `
      <button type="button" class="pager-arrow" data-step="-1" aria-label="上一页">‹</button>
      <span class="pager-pages"></span>
      <button type="button" class="pager-arrow" data-step="1" aria-label="下一页">›</button>
      <span class="pager-info"></span>`;
    table.after(pager);

    const pagesEl = pager.querySelector('.pager-pages');
    const infoEl = pager.querySelector('.pager-info');
    const prevBtn = pager.querySelector('[data-step="-1"]');
    const nextBtn = pager.querySelector('[data-step="1"]');

    function render() {
      const start = (page - 1) * size;
      rows.forEach((row, i) => {
        const visible = i >= start && i < start + size;
        row.hidden = !visible;
        if (visible) {
          // 重新播放行进入动画，翻页手感更连贯
          row.style.animation = 'none';
          void row.offsetWidth;
          row.style.animation =
            `rowIn .3s cubic-bezier(.22,1,.36,1) ${(i - start) * 0.02}s both`;
        }
      });

      pagesEl.textContent = '';
      for (let p = 1; p <= pageCount; p++) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'pager-page' + (p === page ? ' current' : '');
        btn.dataset.page = String(p);
        btn.textContent = String(p);
        pagesEl.appendChild(btn);
      }
      infoEl.textContent = `共 ${rows.length} 条`;
      prevBtn.disabled = page === 1;
      nextBtn.disabled = page === pageCount;
    }

    pager.addEventListener('click', e => {
      const stepBtn = e.target.closest('[data-step]');
      if (stepBtn) {
        page = Math.min(pageCount, Math.max(1, page + Number(stepBtn.dataset.step)));
        render();
        return;
      }
      const pageBtn = e.target.closest('[data-page]');
      if (pageBtn) {
        page = Number(pageBtn.dataset.page);
        render();
      }
    });

    render();
  });

  // ---- 表单提交 loading ----
  document.addEventListener('submit', e => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    const btn = form.querySelector('button[type=submit]:not(.ghost):not(.link):not(.leave)');
    if (!btn) return;
    // 请假小按钮不做 loading（太快，闪烁反而烦）
    btn.disabled = true;
    const original = btn.innerHTML;
    btn.dataset.original = original;
    btn.innerHTML = '<span class="spinner"></span> 处理中…';
    // 若 5 秒后还没跳转（比如校验失败），恢复按钮
    setTimeout(() => {
      if (document.contains(btn)) {
        btn.disabled = false;
        btn.innerHTML = btn.dataset.original;
      }
    }, 5000);
  });

  // ----  spinner 样式注入 ----
  const style = document.createElement('style');
  style.textContent = `
    .spinner {
      display:inline-block;width:14px;height:14px;border-radius:50%;
      border:2px solid rgba(255,255,255,.35);border-top-color:#fff;
      animation:spin .7s linear infinite;vertical-align:-2px;margin-right:6px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  `;
  document.head.appendChild(style);

  // ---- 请假：局部提交，不刷新页面 ----
  // 桌面表格和手机卡片共用这一套。整页提交会让页面闪一下、滚动位置也丢，
  // 所以一律走 fetch：成功后就地切换格子状态，页面完全不动。
  const SCROLL_KEY = 'me.scrollY';
  const LEAVE = 'form[action="/me/leave"]';

  // 兜底：万一 fetch 失败退回整页提交，先记下位置
  document.addEventListener('submit', e => {
    if (e.target.closest(LEAVE)) {
      sessionStorage.setItem(SCROLL_KEY, String(window.scrollY));
    }
  }, true);

  const leaveFormsOf = (day, slot) =>
    [...document.querySelectorAll(LEAVE)].filter(f => {
      const d = f.querySelector('input[name=day]');
      const s = f.querySelector('input[name=slot]');
      return d && s && d.value === day && s.value === slot;
    });

  // 同一份数据渲染了桌面表格和手机卡片两套 DOM，两边一起改才不会对不上
  const applyLeaveState = (day, slot, onLeave) => {
    for (const form of leaveFormsOf(day, slot)) {
      const cell = form.closest('.m-cell, td.cell');
      if (!cell) continue;
      cell.classList.toggle('on-leave', onLeave);
      const btn = form.querySelector('button.leave');
      if (btn) {
        btn.classList.toggle('active', onLeave);
        btn.textContent = onLeave ? '取消请假' : '请假';
      }
      // 桌面格里有「已请假」小标签，跟着一起出现/消失（手机卡片没有）
      const tag = cell.querySelector(':scope > .tag');
      if (onLeave && !tag) {
        const el = document.createElement('div');
        el.className = 'tag';
        el.textContent = '已请假';
        cell.insertBefore(el, form);
      } else if (!onLeave && tag) {
        tag.remove();
      }
    }
    // 顶栏「已请假 N 节」的数跟着变
    const hint = document.querySelector('.weekbar .hint');
    if (hint) {
      const n = document.querySelectorAll('table.grid-desktop td.cell.on-leave').length;
      hint.textContent = hint.textContent.replace(/已请假\s*\d+\s*节/, `已请假 ${n} 节`);
    }
  };

  document.addEventListener('submit', async e => {
    const form = e.target.closest(LEAVE);
    if (!form) return;
    const btn = form.querySelector('button.leave');
    const cell = form.closest('.m-cell, td.cell');
    if (!btn || !cell) return;

    e.preventDefault();
    if (btn.disabled) return;

    const day = form.querySelector('input[name=day]')?.value;
    const slot = form.querySelector('input[name=slot]')?.value;
    if (!day || !slot) { form.submit(); return; }

    btn.disabled = true;
    const original = btn.textContent.trim();
    btn.textContent = '…';

    try {
      const res = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        credentials: 'same-origin',
        redirect: 'follow',
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      // 后端是 toggle 语义：原来没请假的现在请了，反之亦然
      applyLeaveState(day, slot, !cell.classList.contains('on-leave'));
      sessionStorage.removeItem(SCROLL_KEY);
    } catch (err) {
      // 网络抖动或会话过期时退回整页提交，功能不能丢
      btn.textContent = original;
      sessionStorage.setItem(SCROLL_KEY, String(window.scrollY));
      form.submit();
      return;
    } finally {
      btn.disabled = false;
    }
  });

  // ---- 手机端课表卡片：高亮今天 + 位置兜底恢复 ----
  const dayCards = document.querySelectorAll('.grid-mobile .day-card');
  if (dayCards.length) {

    const todayIdx = beijingWeekday();               // 0=周日 1=周一…（按北京时间）
    const dayMap = {1: '周一', 2: '周二', 3: '周三', 4: '周四', 5: '周五', 6: '周六', 0: '周日'};
    const todayName = dayMap[todayIdx];
    let todayCard = null;
    dayCards.forEach(card => {
      const head = card.querySelector('.day-head');
      if (head && head.textContent.trim().startsWith(todayName)) {
        card.classList.add('today');
        const dot = document.createElement('span');
        dot.className = 'today-dot';
        head.appendChild(dot);
        todayCard = card;
      }
    });

    const savedY = sessionStorage.getItem(SCROLL_KEY);
    if (savedY !== null) {
      // 走了整页提交（fetch 失败）才需要恢复位置。
      // 冷加载时 CSS 可能还没到位、卡片区还是 display:none，文档不够高，
      // 一次 scrollTo 会被截断成 0，所以不够高就隔一帧再试。
      sessionStorage.removeItem(SCROLL_KEY);
      const y = parseInt(savedY, 10);
      if (!isNaN(y) && y > 0) {
        let tries = 0;
        const restore = () => {
          window.scrollTo(0, y);
          if (Math.abs(window.scrollY - y) > 8 && tries++ < 15) {
            setTimeout(restore, 60);
          }
        };
        restore();
      }
    } else if (todayCard && window.innerWidth <= 820) {
      // 首次进入：滚到今天
      setTimeout(() => todayCard.scrollIntoView({ block: 'start', behavior: 'smooth' }), 200);
    }
  }

  // ---- 课表十字对位：悬浮/点选格子 → 向上整列 + 向左整行高亮，表头浮起 ----
  // 便于一眼读出"周几 · 第几节"。手机点按可钉住（再点同一格取消）；
  // 只高亮上方与左方（含两个表头），下方/右侧不打扰。
  // 作用于所有课表网格：学生"我的课表"与全员总览（用户确认两边都要）。
  document.querySelectorAll('table.grid').forEach(table => {
    const hasBody = table.querySelector('tbody td.cell');
    if (!hasBody) return;
    let pinned = null;

    const clearMarks = () => {
      table.querySelectorAll('.hl-up, .hl-left, .hl-self, .hl-head').forEach(el => {
        el.classList.remove('hl-up', 'hl-left', 'hl-self', 'hl-head');
      });
    };

    const apply = (td) => {
      clearMarks();
      if (!td) return;
      const ci = td.cellIndex, ri = td.parentElement.rowIndex;
      table.querySelectorAll('tr').forEach(tr => {
        tr.querySelectorAll('th, td').forEach(cell => {
          const c = cell.cellIndex, r = tr.rowIndex;
          if (r === 0 && c === ci) cell.classList.add('hl-head', 'hl-up');
          else if (r === ri && c === 0) cell.classList.add('hl-head', 'hl-left');
          else if (r < ri && c === ci) cell.classList.add('hl-up');
          else if (r === ri && c < ci) cell.classList.add('hl-left');
        });
      });
      td.classList.add('hl-self');
    };

    table.addEventListener('mouseover', e => {
      const td = e.target.closest('td.cell');
      if (td) apply(td);
    });
    table.addEventListener('mouseleave', () => apply(pinned));
    table.addEventListener('click', e => {
      const td = e.target.closest('td.cell');
      if (!td) return;
      pinned = (pinned === td) ? null : td;   // 手机：点住辨认，再点取消
      apply(pinned || td);
    });
  });

  // ---- 主题：按北京时间 08:00–17:00 亮色，其余暗色 ----
  // 首屏主题由服务端算好（见 app/theme.py），页面同时带着「还有多少秒切换」。
  // 前端只按这个倒计时翻面，不读设备时钟——手机时间不准也不会把主题换错。
  (function () {
    const root = document.documentElement;
    const btn = document.getElementById('themeToggle');
    let manual = null; // 只在本次页面有效，刷新就回到自动

    // 旧版本把手动主题存在 localStorage，留着会让人以为规则没生效，清掉
    try { localStorage.removeItem('theme'); } catch (e) { /* 无痕模式 */ }

    const sync = () => {
      if (!btn) return;
      btn.textContent = (root.getAttribute('data-theme') || 'dark') === 'light' ? '☀️' : '🌙';
      btn.title = manual
        ? '已临时切换，刷新后恢复「北京时间 8:00–17:00 亮色」'
        : '按北京时间自动切换（8:00–17:00 亮色），点击可临时改';
    };

    const apply = (t) => {
      root.setAttribute('data-theme', t);
      sync();
    };

    // 图标要跟服务端渲染出来的主题对齐（模板里的默认图标是月亮）
    sync();

    // 翻过一次之后的间隔是固定的：8:00→17:00 九小时，17:00→次日 8:00 十五小时。
    // 所以后续不再需要服务端反复下发，也不会因为设备时钟漂移而算错。
    const LIGHT_SPAN = 9 * 3600;
    const DARK_SPAN = 15 * 3600;

    let autoState = (root.getAttribute('data-theme') || 'dark') === 'light' ? 'light' : 'dark';
    let timer = null;

    const schedule = (seconds) => {
      clearTimeout(timer);
      if (!(seconds > 0)) return;
      timer = setTimeout(() => {
        autoState = autoState === 'light' ? 'dark' : 'light';
        if (!manual) apply(autoState);     // 手动临时切过就不打扰
        schedule(autoState === 'light' ? LIGHT_SPAN : DARK_SPAN);
      }, seconds * 1000);
    };

    if (btn) {
      btn.addEventListener('click', () => {
        manual = (root.getAttribute('data-theme') || 'dark') === 'light' ? 'dark' : 'light';
        apply(manual);
      });
    }

    const initial = parseInt(root.getAttribute('data-theme-switch-in'), 10);
    schedule(isFinite(initial) && initial > 0 ? initial : 0);
  })();
})();
