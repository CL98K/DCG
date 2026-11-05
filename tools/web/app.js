/* DCG Schema Builder —— 前端逻辑（原生 JS，无构建、无第三方依赖） */
(function () {
  'use strict';

  var TYPES = ['int64', 'int32', 'float64', 'str', 'bool', 'list', 'object', 'any'];
  var SHAPES = ['list', 'dict', 'index', 'rows'];
  var MAX_RENDER_ROWS = 200;
  var RESERVED = { rows: true, row: true, r: true, out: true, ap: true, _: true };
  var IDENT_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

  var state = {
    inferred: null,
    fields: [],
    nextId: 1,
    nameTouched: false,
    previewOpen: false,
    lastColumns: []
  };

  var el = {};
  [
    'schemaName', 'shapeSelect', 'healthBadge', 'errorBar', 'errorText', 'errorClose',
    'rawInput', 'demoBtn', 'inferBtn', 'demoHint', 'statusBar', 'warnings', 'treePanel',
    'canvas', 'emptyGuide', 'cardList', 'cardCount', 'canvasStatus', 'previewBtn',
    'runBtn', 'schemaPreviewWrap', 'schemaPreview', 'resultsPanel', 'metaOutCount',
    'metaElapsed', 'metaName', 'metaLines', 'metaChars', 'metaGenerator', 'truncNote',
    'resultEmpty', 'tableWrap', 'resultTable', 'resultWarnings', 'codeOut', 'codeEmpty',
    'resultJson', 'errorEmpty', 'errorDetail', 'errStage', 'errText', 'errCodeWrap', 'errCode'
  ].forEach(function (id) { el[id] = document.getElementById(id); });

  var tabButtons = {
    result: document.getElementById('tab-result'),
    code: document.getElementById('tab-code'),
    error: document.getElementById('tab-error')
  };
  var tabPanes = {
    result: document.getElementById('pane-result'),
    code: document.getElementById('pane-code'),
    error: document.getElementById('pane-error')
  };

  /* ------------------------------------------------------------ 基础工具 */

  function ApiError(message, info) {
    this.name = 'ApiError';
    this.message = message;
    this.stage = (info && info.stage) || 'client';
    this.code = info && info.code != null ? info.code : null;
    this.status = info && info.status != null ? info.status : null;
    this.payload = info && info.payload ? info.payload : null;
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  function clearError() {
    el.errorBar.hidden = true;
    el.errorText.textContent = '';
  }

  function showError(err, focusResults) {
    var stage = (err && err.stage) || 'client';
    var message = err && err.message ? err.message : String(err);
    el.errorText.textContent = '[' + stage + '] ' + message;
    el.errorBar.hidden = false;
    if (focusResults) {
      renderErrorPane(stage, message, err && err.code);
      selectTab('error');
    }
  }

  function fetchJSON(path, options) {
    var opts = options || {};
    return fetch(path, opts).catch(function (err) {
      throw new ApiError('网络错误：无法连接本地服务（' + (err && err.message ? err.message : 'fetch failed') + '）', { stage: 'network' });
    }).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        if (text) {
          try {
            data = JSON.parse(text);
          } catch (parseErr) {
            throw new ApiError('响应不是合法 JSON（HTTP ' + res.status + '）', { stage: 'http', status: res.status });
          }
        }
        if (data && typeof data === 'object' && data.ok === false) {
          throw new ApiError(String(data.error || ('请求失败（HTTP ' + res.status + '）')), {
            stage: data.stage || 'http',
            code: data.code,
            status: res.status,
            payload: data
          });
        }
        if (!res.ok) {
          throw new ApiError('请求失败（HTTP ' + res.status + '）', { stage: 'http', status: res.status, payload: data });
        }
        if (!data || typeof data !== 'object') {
          throw new ApiError('响应缺少 JSON 对象（HTTP ' + res.status + '）', { stage: 'http', status: res.status });
        }
        return data;
      });
    });
  }

  function withBusy(button, label, work) {
    var original = button.textContent;
    button.disabled = true;
    button.textContent = label;
    return Promise.resolve()
      .then(work)
      .then(function (value) {
        button.disabled = false;
        button.textContent = original;
        return value;
      }, function (err) {
        button.disabled = false;
        button.textContent = original;
        throw err;
      });
  }

  function checkJSON(text) {
    if (!text || !text.trim()) {
      throw new ApiError('请先粘贴原始 JSON 文本，或点「载入示例」。', { stage: 'client' });
    }
    try {
      JSON.parse(text);
    } catch (err) {
      throw new ApiError('JSON 解析失败：' + (err && err.message ? err.message : String(err)), { stage: 'json' });
    }
  }

  function sanitizeName(raw) {
    var s = String(raw == null ? '' : raw).trim();
    var m = /^\[(\d+)\]$/.exec(s);
    if (m) return 'col_' + m[1];
    s = s.replace(/[^A-Za-z0-9_]/g, '_');
    s = s.replace(/_+/g, '_').replace(/^_+|_+$/g, '');
    if (!s || /^_*$/.test(s)) s = 'field';
    if (!/^[A-Za-z_]/.test(s)) s = 'f_' + s;
    if (RESERVED[s]) s = s + '_';
    return s;
  }

  function fallbackSuggest(node) {
    var path = String((node && node.path) || '');
    var segs = path.split('.');
    for (var i = segs.length - 1; i >= 0; i -= 1) {
      var seg = segs[i].replace(/^\$/, '');
      if (!seg || /^\d+$/.test(seg)) continue;
      var name = seg.replace(/\[(\d+)\]/g, '_$1').replace(/\]/g, '');
      var cleaned = sanitizeName(name);
      if (cleaned && cleaned !== 'field') return cleaned;
    }
    return sanitizeName((node && node.label) || 'field');
  }

  function uniqueName(base) {
    var name = base;
    var n = 2;
    while (state.fields.some(function (f) { return f.name === name; })) {
      name = base + '_' + n;
      n += 1;
    }
    return name;
  }

  function sampleText(value) {
    var text;
    try {
      text = JSON.stringify(value);
    } catch (err) {
      text = String(value);
    }
    if (text == null) text = String(value);
    if (text.length > 56) text = text.slice(0, 55) + '…';
    return text;
  }

  /* ------------------------------------------------------------ 健康检查 */

  function loadHealth() {
    el.healthBadge.disabled = true;
    el.healthBadge.textContent = 'DCG 检测中…';
    el.healthBadge.className = 'badge neutral';
    fetchJSON('/api/health').then(function (data) {
      var version = data.dcg_version ? String(data.dcg_version) : '未知';
      el.healthBadge.textContent = 'DCG ' + version;
      el.healthBadge.className = 'badge ok';
      el.healthBadge.title = '后端在线：python ' + (data.python || '—') + '，端口 ' + (data.port || '—') + '（点击重新检测）';
    }).catch(function (err) {
      el.healthBadge.textContent = '离线/未知';
      el.healthBadge.className = 'badge bad';
      el.healthBadge.title = '后端未就绪：' + (err.message || '未知错误') + '（点击重试）';
    }).then(function () {
      el.healthBadge.disabled = false;
    });
  }

  /* ------------------------------------------------------------ 载入示例 */

  function loadDemo() {
    clearError();
    withBusy(el.demoBtn, '载入中…', function () {
      return fetchJSON('/api/demo').then(function (data) {
        if (typeof data.text !== 'string') {
          throw new ApiError('示例响应缺少 text 字段。', { stage: 'http' });
        }
        el.rawInput.value = data.text;
        el.demoHint.hidden = false;
        el.demoHint.textContent = data.hint ? '提示：' + data.hint : '示例已载入，点「推断结构」继续。';
        el.rawInput.focus();
      });
    }).catch(function (err) {
      showError(err);
    });
  }

  /* ------------------------------------------------------------ 推断结构 */

  function doInfer() {
    if (el.inferBtn.disabled) return;
    clearError();
    var text = el.rawInput.value;
    try {
      checkJSON(text);
    } catch (localErr) {
      showError(localErr);
      return;
    }
    withBusy(el.inferBtn, '推断中…', function () {
      return fetchJSON('/api/infer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text })
      }).then(applyInfer);
    }).catch(function (err) {
      showError(err);
    });
  }

  function applyInfer(data) {
    state.inferred = data;
    if (!state.nameTouched) {
      var suggested = data.schema_name ? String(data.schema_name).trim() : '';
      el.schemaName.value = suggested || 'schema';
    }
    renderStatus(data);
    renderWarnings(el.warnings, data.warnings, '推断警告：');
    renderTree(data.tree, data.fields);
    var rowText = data.row_count != null ? String(data.row_count) + ' 行' : '行数 —';
    var modeText = data.mode ? String(data.mode) : 'mode —';
    var extra = (data.mode === 'wrapper' && data.source) ? ' · 自动解包 ' + data.source : '';
    el.canvasStatus.textContent = modeText + ' · ' + rowText + extra;
  }

  function renderStatus(data) {
    var parts = [];
    parts.push('mode ' + (data.mode || '—'));
    parts.push('row_count ' + (data.row_count != null ? data.row_count : '—'));
    parts.push('source ' + (data.source || '—'));
    var text = parts.join(' · ');
    if (data.mode === 'wrapper' && data.source) {
      text += ' · 数据按 ' + data.source + ' 自动解包';
    }
    el.statusBar.textContent = text;
  }

  function renderWarnings(listEl, warnings, prefix) {
    listEl.textContent = '';
    var items = Array.isArray(warnings) ? warnings : [];
    if (!items.length) {
      listEl.hidden = true;
      return;
    }
    listEl.hidden = false;
    items.forEach(function (w) {
      var li = document.createElement('li');
      li.textContent = prefix + String(w);
      listEl.appendChild(li);
    });
  }

  /* ------------------------------------------------------------ 结构树 */

  function renderTree(tree, fields) {
    el.treePanel.textContent = '';
    if (!tree || typeof tree !== 'object') {
      el.treePanel.className = 'tree-panel empty';
      var empty = document.createElement('p');
      empty.className = 'empty-text';
      empty.textContent = '没有可用的结构树数据。';
      el.treePanel.appendChild(empty);
      return;
    }
    el.treePanel.className = 'tree-panel';

    var fieldMap = {};
    (Array.isArray(fields) ? fields : []).forEach(function (f) {
      if (f && typeof f.path === 'string') fieldMap[f.path] = f;
    });

    var root = buildTreeNode(tree, 0, fieldMap);
    el.treePanel.appendChild(root);

    var meta = document.createElement('p');
    meta.className = 'tree-meta';
    meta.textContent = '拖拽叶子字段到右侧画布（也可双击或按 Enter 添加）。';
    el.treePanel.appendChild(meta);
  }

  function buildTreeNode(node, depth, fieldMap) {
    var label = node && node.label != null ? String(node.label) : '';
    var type = node && node.type ? String(node.type) : 'any';
    var hasChildren = Array.isArray(node && node.children) && node.children.length > 0;
    var known = node && typeof node.path === 'string' && Object.prototype.hasOwnProperty.call(fieldMap, node.path);
    var isContainerType = type === 'rows' || type === 'object';
    var isLeaf = !hasChildren && (known || !isContainerType);

    if (isLeaf) {
      return buildLeaf(node, type, fieldMap);
    }

    var details = document.createElement('details');
    details.className = 'tree-node';
    details.open = depth < 2;

    var summary = document.createElement('summary');
    summary.className = 'tree-row';
    summary.appendChild(makeLabel(label));
    summary.appendChild(makeBadge('type-badge', type));
    if (node && node.count != null) {
      summary.appendChild(makeBadge('count-badge', '×' + node.count));
    }
    if (node && node.sample != null) {
      summary.appendChild(makeSample(node.sample));
    }
    details.appendChild(summary);

    var children = document.createElement('div');
    children.className = 'tree-children';
    (node.children || []).forEach(function (child) {
      children.appendChild(buildTreeNode(child, depth + 1, fieldMap));
    });
    details.appendChild(children);
    return details;
  }

  function buildLeaf(node, type, fieldMap) {
    var field = (node && typeof node.path === 'string') ? fieldMap[node.path] : null;
    var info = {
      path: (node && typeof node.path === 'string') ? node.path : '',
      label: (field && field.label) || (node && node.label != null ? String(node.label) : ''),
      type: (field && TYPES.indexOf(field.type) !== -1) ? field.type : (TYPES.indexOf(type) !== -1 ? type : 'any'),
      suggest: (field && field.suggest) ? String(field.suggest) : fallbackSuggest(node)
    };

    var row = document.createElement('div');
    row.className = 'tree-row leaf-row';
    row.draggable = true;
    row.tabIndex = 0;
    row.setAttribute('role', 'button');
    row.title = '拖到右侧画布添加字段；路径 ' + info.path + '，建议名 ' + info.suggest + '（双击或 Enter 也可添加）';
    row.setAttribute('aria-label', '字段 ' + info.label + '，类型 ' + info.type + '，建议名 ' + info.suggest + '，拖到画布或按回车添加');
    row.appendChild(makeLabel(info.label));
    row.appendChild(makeBadge('type-badge', info.type));
    if (node && node.count != null) {
      row.appendChild(makeBadge('count-badge', '×' + node.count));
    }
    if (node && node.sample != null) {
      row.appendChild(makeSample(node.sample));
    }
    row.appendChild(makeBadge('suggest-text', '→ ' + info.suggest));

    row.addEventListener('dragstart', function (ev) {
      var payload = JSON.stringify({
        path: info.path,
        label: info.label,
        type: info.type,
        suggest: info.suggest
      });
      try {
        ev.dataTransfer.effectAllowed = 'copy';
        ev.dataTransfer.setData('application/x-dcg-field', payload);
        ev.dataTransfer.setData('text/plain', payload);
      } catch (err) {
        /* 某些环境拒绝自定义类型时，拖拽仍可用 text/plain 兜底 */
      }
      row.classList.add('dragging');
    });
    row.addEventListener('dragend', function () {
      row.classList.remove('dragging');
    });
    row.addEventListener('dblclick', function () {
      addField(info, state.fields.length);
    });
    row.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Spacebar') {
        ev.preventDefault();
        addField(info, state.fields.length);
      }
    });
    return row;
  }

  function makeLabel(text) {
    var span = document.createElement('span');
    span.className = 'tree-label';
    span.textContent = text || '(空)';
    return span;
  }

  function makeBadge(className, text) {
    var span = document.createElement('span');
    span.className = className;
    span.textContent = text;
    return span;
  }

  function makeSample(value) {
    var span = document.createElement('span');
    span.className = 'sample-text';
    span.textContent = sampleText(value);
    span.title = sampleText(value);
    return span;
  }

  /* ------------------------------------------------------------ 画布卡片 */

  function addField(payload, index) {
    if (!payload || typeof payload.path !== 'string' || !payload.path) {
      showError(new ApiError('拖拽数据无效：缺少字段路径。', { stage: 'client' }));
      return;
    }
    var type = TYPES.indexOf(payload.type) !== -1 ? payload.type : 'any';
    var base = payload.suggest ? sanitizeName(payload.suggest) : fallbackSuggest(payload);
    var card = {
      id: 'f' + state.nextId,
      name: uniqueName(base),
      type: type,
      mode: 'path',
      path: payload.path,
      expr: ''
    };
    state.nextId += 1;
    var at = typeof index === 'number' ? index : state.fields.length;
    if (at < 0) at = 0;
    if (at > state.fields.length) at = state.fields.length;
    state.fields.splice(at, 0, card);
    renderCards();
    schedulePreview();
  }

  function renderCards() {
    el.cardList.textContent = '';
    if (!state.fields.length) {
      el.emptyGuide.hidden = false;
      el.cardCount.textContent = '0 个字段';
      return;
    }
    el.emptyGuide.hidden = true;
    el.cardCount.textContent = state.fields.length + ' 个字段';
    state.fields.forEach(function (field, index) {
      el.cardList.appendChild(buildCard(field, index));
    });
  }

  function buildCard(field, index) {
    var card = document.createElement('article');
    card.className = 'field-card';
    card.draggable = true;
    card.dataset.id = field.id;
    card.title = '拖动卡片可重排（在输入框/按钮上拖动不会触发）';

    var head = document.createElement('div');
    head.className = 'card-head';

    var handle = document.createElement('span');
    handle.className = 'card-handle';
    handle.textContent = '≡';
    handle.title = '拖动以重排卡片';
    handle.setAttribute('aria-hidden', 'true');
    head.appendChild(handle);

    var indexBadge = document.createElement('span');
    indexBadge.className = 'card-index';
    indexBadge.textContent = String(index + 1);
    head.appendChild(indexBadge);

    var nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.className = 'name-input';
    nameInput.value = field.name;
    nameInput.spellcheck = false;
    nameInput.autocomplete = 'off';
    nameInput.setAttribute('aria-label', '第 ' + (index + 1) + ' 个字段的输出名');
    nameInput.addEventListener('input', function () {
      field.name = nameInput.value;
      schedulePreview();
    });
    head.appendChild(nameInput);

    var typeSelect = document.createElement('select');
    typeSelect.className = 'type-select';
    typeSelect.setAttribute('aria-label', '字段 ' + (field.name || '') + ' 的输出类型');
    TYPES.forEach(function (t) {
      var opt = document.createElement('option');
      opt.value = t;
      opt.textContent = t;
      if (t === field.type) opt.selected = true;
      typeSelect.appendChild(opt);
    });
    typeSelect.addEventListener('change', function () {
      field.type = typeSelect.value;
      schedulePreview();
    });
    head.appendChild(typeSelect);

    var modeGroup = document.createElement('div');
    modeGroup.className = 'mode-group';
    modeGroup.setAttribute('role', 'group');
    modeGroup.setAttribute('aria-label', '取值方式');
    [['path', '取值路径'], ['expr', 'Python 表达式']].forEach(function (pair) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'mode-btn' + (field.mode === pair[0] ? ' active' : '');
      btn.textContent = pair[1];
      btn.setAttribute('aria-pressed', field.mode === pair[0] ? 'true' : 'false');
      btn.addEventListener('click', function () {
        if (field.mode === pair[0]) return;
        field.mode = pair[0];
        renderCards();
        schedulePreview();
      });
      modeGroup.appendChild(btn);
    });
    head.appendChild(modeGroup);

    var actions = document.createElement('div');
    actions.className = 'card-actions';

    var upBtn = document.createElement('button');
    upBtn.type = 'button';
    upBtn.className = 'icon-btn';
    upBtn.textContent = '↑';
    upBtn.title = '上移字段';
    upBtn.setAttribute('aria-label', '上移字段 ' + (field.name || ''));
    upBtn.disabled = index === 0;
    upBtn.addEventListener('click', function () { moveField(index, -1); });
    actions.appendChild(upBtn);

    var downBtn = document.createElement('button');
    downBtn.type = 'button';
    downBtn.className = 'icon-btn';
    downBtn.textContent = '↓';
    downBtn.title = '下移字段';
    downBtn.setAttribute('aria-label', '下移字段 ' + (field.name || ''));
    downBtn.disabled = index === state.fields.length - 1;
    downBtn.addEventListener('click', function () { moveField(index, 1); });
    actions.appendChild(downBtn);

    var delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'icon-btn danger';
    delBtn.textContent = '×';
    delBtn.title = '删除字段';
    delBtn.setAttribute('aria-label', '删除字段 ' + (field.name || ''));
    delBtn.addEventListener('click', function () {
      state.fields.splice(index, 1);
      renderCards();
      schedulePreview();
    });
    actions.appendChild(delBtn);

    head.appendChild(actions);
    card.appendChild(head);

    var body = document.createElement('div');
    body.className = 'card-body';

    var isExpr = field.mode === 'expr';
    var valueInput = document.createElement('input');
    valueInput.type = 'text';
    valueInput.className = 'value-input';
    valueInput.spellcheck = false;
    valueInput.autocomplete = 'off';
    valueInput.value = isExpr ? field.expr : field.path;
    valueInput.placeholder = isExpr ? '例如 $.qty * 2（自动包成 `…`）' : '例如 $.qty';
    valueInput.setAttribute('aria-label', (isExpr ? 'Python 表达式' : '取值路径') + '：字段 ' + (field.name || ''));
    valueInput.addEventListener('input', function () {
      if (isExpr) field.expr = valueInput.value;
      else field.path = valueInput.value;
      schedulePreview();
    });
    body.appendChild(valueInput);

    var hint = document.createElement('p');
    hint.className = 'card-hint';
    hint.textContent = isExpr
      ? '会作为 DCG expr 自动包进反引号（`…`）；已带反引号则不重复。'
      : '以 $ 开头的 DCG 取值路径，例如 $.sku。';
    body.appendChild(hint);

    card.appendChild(body);

    card.addEventListener('dragstart', function (ev) {
      var target = ev.target;
      if (target && target.closest && target.closest('input, select, button, textarea')) {
        ev.preventDefault();
        return;
      }
      try {
        ev.dataTransfer.effectAllowed = 'move';
        ev.dataTransfer.setData('application/x-dcg-card', field.id);
        ev.dataTransfer.setData('text/plain', 'dcg-card:' + field.id);
      } catch (err) {
        /* 忽略：拖拽类型不可用时由 dragend 清理 */
      }
      card.classList.add('dragging');
    });
    card.addEventListener('dragend', function () {
      card.classList.remove('dragging');
      removeDropIndicator();
      el.canvas.classList.remove('drop-active');
    });

    return card;
  }

  function moveField(index, delta) {
    var to = index + delta;
    if (to < 0 || to >= state.fields.length) return;
    var tmp = state.fields[index];
    state.fields[index] = state.fields[to];
    state.fields[to] = tmp;
    renderCards();
    schedulePreview();
  }

  function moveCard(id, index) {
    var from = -1;
    for (var i = 0; i < state.fields.length; i += 1) {
      if (state.fields[i].id === id) { from = i; break; }
    }
    if (from < 0) return;
    var card = state.fields.splice(from, 1)[0];
    var to = index;
    if (from < to) to -= 1;
    if (to < 0) to = 0;
    if (to > state.fields.length) to = state.fields.length;
    state.fields.splice(to, 0, card);
    renderCards();
    schedulePreview();
  }

  /* ------------------------------------------------------------ 拖拽 */

  function hasDragType(dataTransfer, type) {
    if (!dataTransfer) return false;
    var types = [];
    try {
      types = Array.prototype.slice.call(dataTransfer.types || []);
    } catch (err) {
      types = [];
    }
    return types.indexOf(type) !== -1;
  }

  function readFieldPayload(dataTransfer) {
    if (!dataTransfer) return null;
    var raw = '';
    try { raw = dataTransfer.getData('application/x-dcg-field'); } catch (err) { raw = ''; }
    if (!raw) {
      try { raw = dataTransfer.getData('text/plain'); } catch (err2) { raw = ''; }
    }
    if (!raw || raw.indexOf('dcg-card:') === 0) return null;
    var obj = null;
    try { obj = JSON.parse(raw); } catch (err3) { return null; }
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return null;
    if (typeof obj.path !== 'string') return null;
    return obj;
  }

  function computeDropIndex(clientY) {
    var cards = el.cardList.querySelectorAll('.field-card');
    for (var i = 0; i < cards.length; i += 1) {
      var rect = cards[i].getBoundingClientRect();
      if (clientY < rect.top + rect.height / 2) return i;
    }
    return cards.length;
  }

  function placeDropIndicator(index) {
    removeDropIndicator();
    var indicator = document.createElement('div');
    indicator.className = 'drop-indicator';
    var cards = el.cardList.querySelectorAll('.field-card');
    if (cards[index]) el.cardList.insertBefore(indicator, cards[index]);
    else el.cardList.appendChild(indicator);
  }

  function removeDropIndicator() {
    var existing = el.cardList.querySelector('.drop-indicator');
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
  }

  function onCanvasDragOver(ev) {
    var dt = ev.dataTransfer;
    if (!dt) return;
    var isField = hasDragType(dt, 'application/x-dcg-field');
    var isCard = hasDragType(dt, 'application/x-dcg-card');
    if (!isField && !isCard) return;
    ev.preventDefault();
    try { dt.dropEffect = isField ? 'copy' : 'move'; } catch (err) { /* 忽略 */ }
    el.canvas.classList.add('drop-active');
    placeDropIndicator(computeDropIndex(ev.clientY));
  }

  function onCanvasDragLeave(ev) {
    if (!el.canvas.contains(ev.relatedTarget)) {
      el.canvas.classList.remove('drop-active');
      removeDropIndicator();
    }
  }

  function onCanvasDrop(ev) {
    ev.preventDefault();
    el.canvas.classList.remove('drop-active');
    var index = computeDropIndex(ev.clientY);
    removeDropIndicator();
    var dt = ev.dataTransfer;
    if (!dt) return;

    var fieldPayload = readFieldPayload(dt);
    if (fieldPayload) {
      addField(fieldPayload, index);
      return;
    }

    var cardId = '';
    try { cardId = dt.getData('application/x-dcg-card'); } catch (err) { cardId = ''; }
    if (cardId) {
      moveCard(cardId, index);
      return;
    }
    var plain = '';
    try { plain = dt.getData('text/plain'); } catch (err2) { plain = ''; }
    if (plain && plain.indexOf('dcg-card:') === 0) {
      moveCard(plain.slice('dcg-card:'.length), index);
    }
  }

  /* ------------------------------------------------------------ schema 组装 */

  function buildSchema() {
    var name = String(el.schemaName.value || '').trim();
    if (!name) throw new ApiError('schema 名为空，请填写（例如 orders）。', { stage: 'client' });
    if (!IDENT_RE.test(name)) throw new ApiError('schema 名必须是合法标识符（字母或下划线开头，仅含字母/数字/下划线）：' + name, { stage: 'client' });
    if (RESERVED[name]) throw new ApiError('schema 名不能使用保留名：' + name, { stage: 'client' });

    if (!state.fields.length) {
      throw new ApiError('画布为空：请先从左侧结构树把叶子字段拖进来。', { stage: 'client' });
    }

    var shape = SHAPES.indexOf(el.shapeSelect.value) !== -1 ? el.shapeSelect.value : 'list';
    var fields = {};
    var seen = {};

    state.fields.forEach(function (field, index) {
      var label = '第 ' + (index + 1) + ' 个字段';
      var fname = String(field.name || '').trim();
      if (!fname) throw new ApiError(label + '的输出名为空。', { stage: 'client' });
      if (!IDENT_RE.test(fname)) throw new ApiError(label + '的输出名不是合法标识符：' + fname, { stage: 'client' });
      if (RESERVED[fname]) throw new ApiError(label + '的输出名与 DCG 内建名冲突：' + fname, { stage: 'client' });
      if (seen[fname]) throw new ApiError('输出名重复：' + fname, { stage: 'client' });
      seen[fname] = true;

      var type = TYPES.indexOf(field.type) !== -1 ? field.type : 'any';
      if (field.mode === 'expr') {
        var raw = String(field.expr || '').trim();
        if (!raw) throw new ApiError(label + '（' + fname + '）缺少 Python 表达式。', { stage: 'client' });
        var expr = raw;
        if (!(raw.length >= 2 && raw.charAt(0) === '`' && raw.charAt(raw.length - 1) === '`')) {
          expr = '`' + raw + '`';
        }
        fields[fname] = { type: type, expr: expr };
      } else {
        var path = String(field.path || '').trim();
        if (!path) throw new ApiError(label + '（' + fname + '）缺少取值路径。', { stage: 'client' });
        if (path.charAt(0) !== '$') {
          throw new ApiError(label + '（' + fname + '）取值路径必须以 $ 开头，例如 $.sku；当前：' + path, { stage: 'client' });
        }
        fields[fname] = { type: type, value: path };
      }
    });

    var schema = { name: name };
    if (state.inferred && state.inferred.mode === 'wrapper' && state.inferred.source) {
      schema.input = { source: String(state.inferred.source) };
    }
    schema.output = { shape: shape, trace: false };
    schema.out = { type: 'object', fields: fields };
    return schema;
  }

  var previewTimer = 0;

  function schedulePreview() {
    if (!state.previewOpen) return;
    if (previewTimer) window.clearTimeout(previewTimer);
    previewTimer = window.setTimeout(function () {
      previewTimer = 0;
      try {
        el.schemaPreview.textContent = JSON.stringify(buildSchema(), null, 2);
      } catch (err) {
        /* 编辑中间态无效时不打扰用户，保留上一次预览 */
      }
    }, 250);
  }

  function togglePreview() {
    state.previewOpen = !state.previewOpen;
    el.previewBtn.setAttribute('aria-expanded', state.previewOpen ? 'true' : 'false');
    if (!state.previewOpen) {
      el.schemaPreviewWrap.hidden = true;
      return;
    }
    try {
      var schema = buildSchema();
      el.schemaPreview.textContent = JSON.stringify(schema, null, 2);
      el.schemaPreviewWrap.hidden = false;
      clearError();
    } catch (err) {
      el.schemaPreviewWrap.hidden = true;
      showError(err);
    }
  }

  /* ------------------------------------------------------------ 运行测试 */

  function doRun() {
    if (el.runBtn.disabled) return;
    clearError();
    var text = el.rawInput.value;
    try {
      checkJSON(text);
    } catch (localErr) {
      showError(localErr);
      return;
    }

    var schema;
    try {
      schema = buildSchema();
    } catch (schemaErr) {
      showError(schemaErr);
      return;
    }

    var originalLabel = el.runBtn.textContent;
    el.runBtn.disabled = true;
    el.runBtn.textContent = '运行中…';
    el.previewBtn.disabled = true;

    fetchJSON('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text, schema: schema })
    }).then(function (data) {
      state.lastColumns = Object.keys(schema.out.fields);
      renderRunSuccess(data, schema);
      if (el.resultsPanel && el.resultsPanel.scrollIntoView) {
        el.resultsPanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }).catch(function (err) {
      renderRunFailure(err);
      showError(err, true);
    }).then(function () {
      el.runBtn.disabled = false;
      el.runBtn.textContent = originalLabel;
      el.previewBtn.disabled = false;
    });
  }

  function fieldNames(schema) {
    if (schema && schema.out && schema.out.fields) return Object.keys(schema.out.fields);
    return state.lastColumns.slice();
  }

  function isPlainObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  function unwrapSingleObject(out) {
    if (Array.isArray(out)) {
      if (out.length === 1 && isPlainObject(out[0])) return out[0];
      return null;
    }
    return isPlainObject(out) ? out : null;
  }

  function columnName(names, index) {
    return names[index] || ('col_' + index);
  }

  function rawView(data, message) {
    var text;
    try {
      text = JSON.stringify(data.out, null, 2);
    } catch (err) {
      text = String(data.out);
    }
    return {
      kind: 'raw',
      json: text,
      message: message,
      totalRows: typeof data.out_count === 'number' ? data.out_count : null,
      serverCount: typeof data.out_count === 'number' ? data.out_count : null,
      truncated: !!data.truncated
    };
  }

  function buildResultView(data, schema) {
    var shape = (schema && schema.output && SHAPES.indexOf(schema.output.shape) !== -1)
      ? schema.output.shape
      : 'list';
    var names = fieldNames(schema);
    var serverCount = typeof data.out_count === 'number' ? data.out_count : null;
    var out = data.out;

    if (shape === 'list' || shape === 'rows') {
      if (!Array.isArray(out)) {
        return rawView(data, '结果形状与所选 shape（' + shape + '）不符，已回退为格式化 JSON。');
      }
      if (!out.length) {
        return {
          kind: 'empty',
          message: '运行成功，但 out 为空（0 行）。',
          totalRows: 0,
          serverCount: serverCount,
          truncated: false
        };
      }

      var wantsObject = shape === 'rows';
      var consistent = out.every(function (row) {
        return wantsObject ? isPlainObject(row) : Array.isArray(row);
      });
      if (!consistent) {
        return rawView(data, '结果形状与所选 shape（' + shape + '）不符，已回退为格式化 JSON。');
      }

      var width = names.length;
      if (!width) {
        out.forEach(function (row) {
          if (Array.isArray(row)) width = Math.max(width, row.length);
          else if (isPlainObject(row)) width = Math.max(width, Object.keys(row).length);
        });
      }
      var columns = [];
      for (var c = 0; c < width; c += 1) columns.push({ name: columnName(names, c) });

      var renderCount = Math.min(out.length, MAX_RENDER_ROWS);
      var rows = [];
      for (var i = 0; i < renderCount; i += 1) {
        var row = out[i];
        var cells = [];
        for (var j = 0; j < width; j += 1) {
          if (wantsObject) {
            cells.push(row[names[j] || Object.keys(row)[j]]);
          } else {
            cells.push(row[j]);
          }
        }
        rows.push(cells);
      }
      var totalRows = serverCount != null ? serverCount : out.length;
      return {
        kind: 'table',
        columns: columns,
        rows: rows,
        totalRows: totalRows,
        serverCount: serverCount,
        truncated: !!data.truncated || totalRows > MAX_RENDER_ROWS
      };
    }

    if (shape === 'dict') {
      var dict = unwrapSingleObject(out);
      if (!dict) return rawView(data, '结果形状与所选 shape（dict）不符，已回退为格式化 JSON。');
      var keys = Object.keys(dict);
      if (!keys.length) {
        return {
          kind: 'empty',
          message: '运行成功，但 dict 结果没有任何输出列。',
          totalRows: 0,
          serverCount: serverCount,
          truncated: false
        };
      }

      var lengths = keys.map(function (key) {
        return Array.isArray(dict[key]) ? dict[key].length : 1;
      });
      var dictTotal = Math.max.apply(null, lengths);
      var dictRows = [];
      var dictRender = Math.min(dictTotal, MAX_RENDER_ROWS);
      for (var r = 0; r < dictRender; r += 1) {
        dictRows.push(keys.map(function (key) {
          var column = dict[key];
          if (Array.isArray(column)) return column[r];
          return r === 0 ? column : undefined;
        }));
      }
      return {
        kind: 'table',
        columns: keys.map(function (key) { return { name: key }; }),
        rows: dictRows,
        totalRows: dictTotal,
        serverCount: serverCount,
        truncated: !!data.truncated || dictTotal > MAX_RENDER_ROWS
      };
    }

    if (shape === 'index') {
      var indexed = unwrapSingleObject(out);
      if (!indexed) return rawView(data, '结果形状与所选 shape（index）不符，已回退为格式化 JSON。');
      var indexKeys = Object.keys(indexed).filter(function (key) { return /^\d+$/.test(key); });
      if (!indexKeys.length) {
        return rawView(data, '结果形状与所选 shape（index）不符（没有行号键），已回退为格式化 JSON。');
      }
      indexKeys.sort(function (a, b) { return Number(a) - Number(b); });

      var indexWidth = names.length;
      if (!indexWidth) {
        var first = indexed[indexKeys[0]];
        if (Array.isArray(first)) indexWidth = first.length;
        else if (isPlainObject(first)) indexWidth = Object.keys(first).length;
      }
      var indexColumns = [];
      for (var k = 0; k < indexWidth; k += 1) indexColumns.push({ name: columnName(names, k) });

      var indexRender = Math.min(indexKeys.length, MAX_RENDER_ROWS);
      var indexRows = [];
      for (var q = 0; q < indexRender; q += 1) {
        var value = indexed[indexKeys[q]];
        var cells2 = [];
        for (var m = 0; m < indexWidth; m += 1) {
          if (Array.isArray(value)) cells2.push(value[m]);
          else if (isPlainObject(value)) cells2.push(value[names[m] || Object.keys(value)[m]]);
          else cells2.push(m === 0 ? value : undefined);
        }
        indexRows.push(cells2);
      }
      return {
        kind: 'table',
        columns: indexColumns,
        rows: indexRows,
        totalRows: indexKeys.length,
        serverCount: serverCount,
        truncated: !!data.truncated || indexKeys.length > MAX_RENDER_ROWS
      };
    }

    return rawView(data, '未知的输出 shape，已回退为格式化 JSON。');
  }

  function formatCell(value) {
    if (value === undefined) return { text: '—', kind: 'empty' };
    if (value === null) return { text: 'null', kind: 'null' };
    if (typeof value === 'string') return { text: value, kind: '' };
    if (typeof value === 'number' || typeof value === 'boolean') return { text: String(value), kind: '' };
    var text;
    try {
      text = JSON.stringify(value);
    } catch (err) {
      text = String(value);
    }
    if (text === undefined) text = String(value);
    return { text: text, kind: '' };
  }

  function renderTable(view) {
    var thead = el.resultTable.querySelector('thead');
    var tbody = el.resultTable.querySelector('tbody');
    thead.textContent = '';
    tbody.textContent = '';

    var headRow = document.createElement('tr');
    view.columns.forEach(function (col) {
      var th = document.createElement('th');
      th.scope = 'col';
      th.textContent = col.name;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);

    view.rows.forEach(function (row) {
      var tr = document.createElement('tr');
      view.columns.forEach(function (col, index) {
        var cell = formatCell(row[index]);
        var td = document.createElement('td');
        td.textContent = cell.text;
        if (cell.kind === 'empty') td.className = 'cell-empty';
        else if (cell.kind === 'null') td.className = 'cell-null';
        if (cell.text.length > 40) td.title = cell.text;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  function renderRunSuccess(data, schema) {
    var view = buildResultView(data, schema);

    el.metaOutCount.textContent = view.totalRows != null ? String(view.totalRows) : '—';
    el.metaOutCount.title = view.serverCount != null
      ? '行数（服务端 out_count：' + view.serverCount + '）'
      : '行数';
    el.metaElapsed.textContent = typeof data.elapsed_ms === 'number' ? data.elapsed_ms.toFixed(2) + ' ms' : '—';
    var manifest = (data.manifest && typeof data.manifest === 'object') ? data.manifest : {};
    el.metaName.textContent = manifest.name != null ? String(manifest.name) : '—';
    el.metaLines.textContent = manifest.lines != null ? String(manifest.lines) : '—';
    el.metaChars.textContent = manifest.chars != null ? String(manifest.chars) : '—';
    el.metaGenerator.textContent = manifest.generator_version != null ? String(manifest.generator_version) : '—';
    el.truncNote.hidden = !view.truncated;

    el.resultJson.hidden = true;
    el.resultJson.textContent = '';
    el.tableWrap.hidden = true;
    var thead = el.resultTable.querySelector('thead');
    var tbody = el.resultTable.querySelector('tbody');
    thead.textContent = '';
    tbody.textContent = '';

    if (view.kind === 'table') {
      el.resultEmpty.hidden = true;
      el.tableWrap.hidden = false;
      renderTable(view);
    } else if (view.kind === 'raw') {
      el.resultEmpty.hidden = false;
      el.resultEmpty.textContent = view.message;
      el.resultJson.textContent = view.json;
      el.resultJson.hidden = false;
    } else {
      el.resultEmpty.hidden = false;
      el.resultEmpty.textContent = view.message;
    }

    renderWarnings(el.resultWarnings, data.warnings, '警告：');

    if (typeof data.code === 'string' && data.code) {
      el.codeOut.textContent = data.code;
      el.codeOut.hidden = false;
      el.codeEmpty.hidden = true;
    } else {
      el.codeOut.textContent = '';
      el.codeOut.hidden = true;
      el.codeEmpty.hidden = false;
      el.codeEmpty.textContent = '本次响应未返回 code。';
    }

    el.errorEmpty.hidden = false;
    el.errorDetail.hidden = true;
    el.errCodeWrap.hidden = true;
    el.errCode.textContent = '';
    selectTab('result');
  }

  function renderErrorPane(stage, message, code) {
    el.errorEmpty.hidden = true;
    el.errorDetail.hidden = false;
    el.errStage.textContent = stage || '—';
    el.errText.textContent = message || '—';
    if (typeof code === 'string' && code) {
      el.errCode.textContent = code;
      el.errCodeWrap.hidden = false;
    } else {
      el.errCode.textContent = '';
      el.errCodeWrap.hidden = true;
    }
  }

  function renderRunFailure(err) {
    el.metaOutCount.textContent = '—';
    el.metaElapsed.textContent = '—';
    el.metaName.textContent = '—';
    el.metaLines.textContent = '—';
    el.metaChars.textContent = '—';
    el.metaGenerator.textContent = '—';
    el.truncNote.hidden = true;

    el.tableWrap.hidden = true;
    el.resultJson.hidden = true;
    el.resultJson.textContent = '';
    el.resultEmpty.hidden = false;
    el.resultEmpty.textContent = '运行失败，详见「错误」标签。';
    el.resultWarnings.hidden = true;

    el.codeOut.hidden = true;
    el.codeOut.textContent = '';
    el.codeEmpty.hidden = false;
    el.codeEmpty.textContent = '运行失败；若服务端已生成代码，会显示在「错误」标签中。';

    var stage = (err && err.stage) || 'client';
    var message = (err && err.message) || String(err);
    renderErrorPane(stage, message, err && err.code);
    selectTab('error');
  }

  /* ------------------------------------------------------------ tab 切换 */

  function selectTab(name) {
    var keys = ['result', 'code', 'error'];
    keys.forEach(function (key) {
      var active = key === name;
      var btn = tabButtons[key];
      var pane = tabPanes[key];
      if (!btn || !pane) return;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
      btn.tabIndex = active ? 0 : -1;
      pane.hidden = !active;
    });
  }

  function initTabs() {
    tabButtons.result.addEventListener('click', function () { selectTab('result'); });
    tabButtons.code.addEventListener('click', function () { selectTab('code'); });
    tabButtons.error.addEventListener('click', function () { selectTab('error'); });

    Object.keys(tabButtons).forEach(function (key) {
      tabButtons[key].addEventListener('keydown', function (ev) {
        if (ev.key !== 'ArrowRight' && ev.key !== 'ArrowLeft' && ev.key !== 'Home' && ev.key !== 'End') return;
        ev.preventDefault();
        var keys = ['result', 'code', 'error'];
        var current = keys.indexOf(key);
        var next = current;
        if (ev.key === 'ArrowRight') next = (current + 1) % keys.length;
        else if (ev.key === 'ArrowLeft') next = (current - 1 + keys.length) % keys.length;
        else if (ev.key === 'Home') next = 0;
        else if (ev.key === 'End') next = keys.length - 1;
        selectTab(keys[next]);
        tabButtons[keys[next]].focus();
      });
    });
  }

  /* ------------------------------------------------------------ 事件绑定 */

  function init() {
    initTabs();

    el.healthBadge.addEventListener('click', loadHealth);
    el.demoBtn.addEventListener('click', loadDemo);
    el.inferBtn.addEventListener('click', doInfer);
    el.previewBtn.addEventListener('click', togglePreview);
    el.runBtn.addEventListener('click', doRun);
    el.errorClose.addEventListener('click', clearError);

    el.schemaName.addEventListener('input', function () {
      state.nameTouched = true;
      schedulePreview();
    });
    el.shapeSelect.addEventListener('change', schedulePreview);

    el.rawInput.addEventListener('keydown', function (ev) {
      if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') {
        ev.preventDefault();
        doInfer();
      }
    });

    el.canvas.addEventListener('dragover', onCanvasDragOver);
    el.canvas.addEventListener('dragleave', onCanvasDragLeave);
    el.canvas.addEventListener('drop', onCanvasDrop);

    loadHealth();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
