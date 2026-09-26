/* 前端交互：标签输入、复制、批量选择、客户选择器、确认对话框 */
(function () {
  'use strict';

  function q(sel, root) { return (root || document).querySelector(sel); }
  function qa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function tr(key) { return (window.I18N && window.I18N[key]) || key; }

  /* ---------- 复制文本 ---------- */
  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) { /* ignore */ }
    document.body.removeChild(ta);
    return Promise.resolve();
  }

  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-copy]');
    if (!btn) return;
    var text = btn.getAttribute('data-copy') || '';
    copyText(text).then(function () {
      var old = btn.textContent;
      btn.textContent = tr('Copied');
      setTimeout(function () { btn.textContent = old; }, 1500);
    });
  });

  /* ---------- OV 确认对话框 ---------- */
  window.confirmOv = function (msg) {
    return window.confirm(msg || tr('You are submitting an "Organization Verification" request. Continue?'));
  };

  /* ---------- 标签输入 ---------- */
  function initTagInput(host) {
    var name = host.getAttribute('data-name') || 'tags';
    var plain = host.getAttribute('data-plain') === '1'; /* 不做域名小写/去协议处理 */
    var initial = (host.getAttribute('data-value') || '')
      .split(',')
      .map(function (s) { return s.trim(); })
      .filter(Boolean);

    var wrap = document.createElement('div');
    var list = document.createElement('div');
    list.className = 'tag-list';
    var input = document.createElement('input');
    input.type = 'text';
    input.placeholder = tr('Enter and press Enter to add');
    var hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.name = name;

    var tags = initial.slice();

    function sync() {
      hidden.value = tags.join(',');
      list.innerHTML = '';
      tags.forEach(function (t, idx) {
        var span = document.createElement('span');
        span.className = 'tag';
        span.appendChild(document.createTextNode(t));
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = '×';
        btn.title = tr('Remove');
        btn.addEventListener('click', function () {
          tags.splice(idx, 1);
          sync();
        });
        span.appendChild(btn);
        list.appendChild(span);
      });
    }

    function clean(raw) {
      var v = String(raw || '').trim();
      if (!v) return '';
      if (plain) return v;
      return v.toLowerCase().replace(/^https?:\/\//, '').replace(/\/.*$/, '');
    }

    function add(raw) {
      var values = plain
        ? String(raw || '').split(',').map(clean).filter(Boolean)
        : String(raw || '').split(/[\s,;]+/).map(clean).filter(Boolean);
      values.forEach(function (v) {
        if (v && tags.indexOf(v) === -1) tags.push(v);
      });
      sync();
    }

    input.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ',' || ev.key === ';') {
        ev.preventDefault();
        add(input.value);
        input.value = '';
      } else if (ev.key === 'Backspace' && !input.value && tags.length) {
        tags.pop();
        sync();
      }
    });
    input.addEventListener('blur', function () {
      if (input.value.trim()) { add(input.value); input.value = ''; }
    });

    wrap.appendChild(list);
    wrap.appendChild(input);
    wrap.appendChild(hidden);
    host.innerHTML = '';
    host.appendChild(wrap);
    sync();
  }

  /* ---------- 客户选择器（datalist） ---------- */
  function initCustomerPicker() {
    var search = q('#customer_search');
    var hidden = q('#customer_id');
    if (!search || !hidden) return;
    var listId = search.getAttribute('list');
    if (!listId) return;
    var datalist = document.getElementById(listId);
    if (!datalist) return;

    function apply() {
      var value = search.value.trim();
      var match = null;
      qa('option', datalist).forEach(function (opt) {
        if (opt.value === value) match = opt;
      });
      var hint = q('#customer-hint');
      if (match) {
        hidden.value = match.getAttribute('data-id') || '';
        if (hint) {
          hint.textContent = tr('Selected:') + ' ' + match.value;
          hint.style.color = 'var(--ok)';
        }
      } else {
        hidden.value = '';
        if (hint) {
          hint.textContent = tr('No matching customer. Please pick one from the dropdown list.');
          hint.style.color = 'var(--err)';
        }
      }
    }

    search.addEventListener('change', apply);
    search.addEventListener('blur', apply);
    if (hidden.value) {
      qa('option', datalist).forEach(function (opt) {
        if (opt.getAttribute('data-id') === hidden.value) search.value = opt.value;
      });
    }
  }

  /* ---------- 批量选择 ---------- */
  function initBulk() {
    var all = q('#check-all');
    if (all) {
      all.addEventListener('change', function () {
        qa('.row-check').forEach(function (cb) { cb.checked = all.checked; });
      });
    }
    var btn = q('[data-bulk-delete]');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var ids = qa('.row-check').filter(function (cb) { return cb.checked; })
        .map(function (cb) { return cb.value; });
      if (!ids.length) { alert(tr('Select users to delete first')); return; }
      if (!confirm(tr('Confirm deleting the selected %d users?').replace('%d', ids.length))) return;
      var form = document.createElement('form');
      form.method = 'post';
      form.action = btn.getAttribute('data-bulk-delete');
      var input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'ids';
      input.value = ids.join(',');
      form.appendChild(input);
      document.body.appendChild(form);
      form.submit();
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    qa('.tag-input').forEach(initTagInput);
    initCustomerPicker();
    initBulk();
  });
})();
