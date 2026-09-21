'use strict';

// Existing service contract and browser storage keys are retained.
const $ = id => document.getElementById(id);
const icon = name => {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS(svg.namespaceURI, 'use');
  svg.classList.add('icon'); svg.setAttribute('aria-hidden', 'true');
  use.setAttribute('href', `#i-${name}`); svg.append(use); return svg;
};
const element = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
const welcome = $('welcomeView');
let settings = {}, sid = null, busy = false, navigating = false, initialized = false;
let sessions = [], token = '', storagePrefix = 'knowledgeAssistant', currentPage = 'chat';
let activeSources = [], selectedAnswer = '', activeSocket = null, navVersion = 0;
let statusRequest = false, fileRequest = false, lastFileSignature = '', toastTimer;
const selectedFiles = { document: null, faq: null };
const uploading = { document: false, faq: false };
const storageKey = name => `${storagePrefix}:${name}`;
const statuses = { partial: '部分有依据', conflicted: '资料存在分歧', answered: '当前证据支持', faq: '常见问答', greeting: '问候', insufficient: '资料不足', clarify: '需要澄清', empty: '尚未导入资料', indexing: '正在索引', index_failed: '索引失败', error: '回答失败' };

function safeSet(storage, key, value) {
  try { storage.setItem(key, value); } catch (_) { notify('浏览器存储不可用，刷新后可能无法恢复当前会话。'); }
}
function safeGet(storage, key) { try { return storage.getItem(key); } catch (_) { return null; } }
function forgetToken() { token = ''; try { sessionStorage.removeItem(storageKey('Admin')); } catch (_) { /* private browser mode */ } loginView(); }
function notify(text) {
  clearTimeout(toastTimer); $('toast').textContent = text; $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4500);
}
async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, options); }
  catch (_) { throw new Error('无法连接服务，请检查服务是否运行。'); }
  let result;
  try { result = await response.json(); }
  catch (_) { throw new Error(`服务返回了无法读取的响应（${response.status}）。`); }
  if (!response.ok) {
    const error = new Error(typeof result.detail === 'string' ? result.detail : `请求失败（${response.status}），请检查输入。`);
    error.status = response.status; throw error;
  }
  return result;
}
function makeButton(text, action, className = 'subtle-button', iconName) {
  const b = element('button', className); b.type = 'button';
  if (iconName) b.append(icon(iconName));
  if (text) b.append(element('span', '', text));
  b.addEventListener('click', () => Promise.resolve().then(action).catch(e => notify(e.message)));
  return b;
}
function updateControls() {
  const blocked = busy || navigating || !initialized;
  $('sendBtn').disabled = blocked || !$('chatInput').value.trim();
  $('newSessionBtn').disabled = blocked;
  $('clearHistoryBtn').disabled = blocked || !sid;
  $('chatInput').disabled = !initialized;
  $('chatInput').setAttribute('aria-busy', String(busy));
  $('charCount').textContent = `${$('chatInput').value.length} / 2000`;
  $('sendBtn').setAttribute('aria-label', busy ? '正在生成回答' : '发送问题');
}
function remember() {
  if (sid) safeSet(localStorage, storageKey('Current'), sid);
  safeSet(localStorage, storageKey('Sessions'), JSON.stringify(sessions));
  renderSessions();
}
function renderSessions() {
  $('historyList').replaceChildren();
  $('sessionCount').textContent = sessions.length;
  const filter = $('searchBox').value.trim().toLowerCase();
  const list = sessions.filter(s => s.title.toLowerCase().includes(filter)).slice().reverse();
  if (!list.length) $('historyList').append(element('p', 'history-empty', filter ? '没有找到匹配的会话' : '暂无会话'));
  list.forEach(s => {
    const b = makeButton(s.title, async () => {
      if (busy || navigating) return;
      showPage('chat'); await openSession(s.id); closeSidebar();
    }, `history-item${s.id === sid ? ' active' : ''}`, 'chat');
    b.disabled = busy || navigating;
    b.title = s.title;
    if (s.id === sid) b.setAttribute('aria-current', 'true');
    $('historyList').append(b);
  });
  if (currentPage === 'chat') $('threadTitle').textContent = sessions.find(s => s.id === sid)?.title || '新会话';
}
function setSourcePanel(open) {
  $('evidencePanel').hidden = !open;
  $('sourcesToggle').setAttribute('aria-expanded', String(open));
  if (open) renderEvidence();
}
function chooseSources(sources, question, index = -1, reveal = true) {
  activeSources = sources || []; selectedAnswer = question || '';
  $('sourceCount').textContent = activeSources.length;
  renderEvidence(index);
  if (reveal) { $('evidencePanel').hidden = false; $('sourcesToggle').setAttribute('aria-expanded', 'true'); }
}
function renderEvidence(selected = -1) {
  $('evidenceCount').textContent = activeSources.length;
  $('evidenceDescription').textContent = activeSources.length
    ? (selectedAnswer ? `对应问题：${selectedAnswer}` : '')
    : '';
  const list = $('evidenceList'); list.replaceChildren();
  if (!activeSources.length) {
    const empty = element('div', 'evidence-empty'); empty.append(element('p', '', '暂无引用')); list.append(empty); return;
  }
  activeSources.forEach((source, i) => {
    const card = element('article', `evidence-item${i === selected ? ' selected' : ''}`);
    const heading = element('div', 'evidence-title');
    heading.append(element('span', 'evidence-number', i + 1), element('strong', '', source.type === 'faq' ? '常见问答' : (source.file_name || '来源文件')));
    card.append(heading);
    const location = source.type === 'faq' ? source.title : [source.section, source.page ? `第 ${source.page} 页` : '', source.slide ? `幻灯片 ${source.slide}` : ''].filter(Boolean).join(' · ');
    if (location) card.append(element('p', 'location', location));
    if (source.available === false) card.append(element('p', 'stale', '历史引用：当前资料已不可用'));
    card.append(element('blockquote', '', source.quote || '由管理员导入的常见问答。此来源记录未保存独立的原文摘录。'));
    if (source.document_version) {
      card.append(element('p', 'location', `版本 ${source.document_version} · 引用${source.citation_validity === 'valid' ? '可定位' : '未验证'}`));
      const loc = source.locator || {};
      card.append(element('p', 'location', `原文段 ${loc.segment_index ?? '—'} · 字符 ${loc.start ?? '—'}–${loc.end ?? '—'} · ${loc.kind || ''}`));
      card.append(element('p', 'location', `适用时间：${source.effective_from || '起始未知'} 至 ${source.effective_to || '结束未知'}（结束日期不含）`));
      card.append(element('p', 'location', `适用范围：${Object.entries(source.scope || {}).map(([k,v]) => k + '：' + v).join(' · ') || '未注明'} · 来源组：${source.source_group || '未知'}`));
      card.append(element('p', 'location', `可使用性：${source.usability?.usable ? '符合本次访问、时间与范围约束' : '不可用或待确认'} · 权威依据：${source.authority?.basis || '未知'}`));
      if (/^https?:\/\//.test(source.source_url || '')) {const link=element('a','','打开来源地址');link.href=source.source_url;link.target='_blank';link.rel='noopener noreferrer';card.append(link);}
    }
    if (source.id) card.append(element('p', 'chunk-id', `${source.type === 'faq' ? 'FAQ' : '片段'} · ${source.id}`));
    list.append(card);
    if (i === selected) requestAnimationFrame(() => card.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
  });
}
function renderAnswerText(target, text, sources = [], question = '') {
  target.replaceChildren();
  // Small, safe presentation layer: no raw HTML or model-generated links are rendered.
  const parts = String(text).split(/(`[^`\n]+`|\[\d+\])/g);
  parts.forEach(part => {
    if (part.startsWith('`') && part.endsWith('`')) target.append(element('code', '', part.slice(1, -1)));
    else if (/^\[\d+\]$/.test(part) && sources[Number(part.slice(1, -1)) - 1]) {
      const index = Number(part.slice(1, -1)) - 1;
      const b = makeButton(String(index + 1), () => chooseSources(sources, question, index), 'inline-citation');
      b.setAttribute('aria-label', `查看引用 ${index + 1}`); target.append(b);
    } else target.append(document.createTextNode(part));
  });
}
function scrollToLatest(force = false) {
  const node = $('chatHistory');
  if (force || node.scrollHeight - node.scrollTop - node.clientHeight < 150) node.scrollTop = node.scrollHeight;
}
function message(role, text, result = {}, question = '') {
  if (welcome.parentNode) welcome.remove();
  const box = element('article', `message ${role === 'user' ? 'user-message' : 'assistant-message'}`);
  const head = element('div', 'message-header');
  if (role !== 'user') {
    box.setAttribute('aria-label', settings.name || '知识问答助手');
    const label = element('span', 'answer-label', statuses[result.status] ?? '正在检索…');
    head.append(label); box.append(head);
  }
  const content = element('div', 'message-content');
  const body = element('div', 'answer-body');
  content.append(body); box.append(content); $('chatHistory').append(box);
  const view = { box, head, content, body, question, footer: null };
  if (role === 'user') body.textContent = text;
  else if (result.status) finishMessage(view, { ...result, answer: text }, false);
  else {
    const loading = element('div', 'answer-loading');
    const dots = element('span', 'loading-dots'); dots.append(element('i'), element('i'), element('i'));
    loading.append(dots, element('span', '', '检索资料并核对原文…')); body.append(loading);
  }
  scrollToLatest(true); return view;
}
function finishMessage(view, result, select = true) {
  // A failed final event replaces both provisional prose and sources.
  if (result.validation_status === 'failed') result = {...result, status:'error',answer:'回答未通过最终校验，当前无法确认。',sources:[],citations:[],claims:[],conflicts:[]};
  view.head.querySelector('.answer-label').textContent = result.validation_status === 'pending' ? '生成中 / 待校验' : (statuses[result.status] ?? '回答');
  view.content.querySelector('.claim-details')?.remove();
  view.content.querySelector('.loading-step')?.remove();
  view.body.className = `answer-body${['insufficient', 'clarify', 'empty', 'indexing', 'index_failed', 'error'].includes(result.status) ? ' status-message' : ''}${result.status === 'error' ? ' error' : ''}`;
  renderAnswerText(view.body, result.answer, result.sources || [], view.question);
  const details = element('div','claim-details');
  const labels = {supported:'当前证据支持',partial:'部分支持',missing:'无法确认',conflicted:'存在分歧'};
  (result.claims || []).forEach(c => details.append(element('p','claim-state',`${labels[c.status] || c.status} · ${c.text}（${c.reason}）`)));
  (result.conflicts || []).forEach(g => {
    const section=element('section','conflict-detail');section.append(element('strong','',g.status==='adjudicated'?'已人工裁决（保留各方资料）':'资料分歧'));
    g.statements.forEach(c=> {const row=element('p','',c.text+' ');c.evidence_ids.forEach(id=>{const i=(result.sources||[]).findIndex(s=>s.evidence_id===id);if(i>=0)row.append(makeButton(`来源 ${i+1}`,()=>chooseSources(result.sources,view.question,i),'inline-citation'));});section.append(row);});
    section.append(element('p','',g.adjudication_basis?.basis || g.reason));details.append(section);
  });
  (result.missing_information || []).forEach(m=>details.append(element('p','missing-detail',`待补充：${m.question || m.field || ''} · ${m.needed || m.reason}`)));
  if(result.retrieval_status?.index_status && result.retrieval_status.index_status!=='ready')details.append(element('p','missing-detail',`索引状态：${result.retrieval_status.index_status}`));
  if(details.childNodes.length)view.content.append(details);
  if (view.footer) view.footer.remove();
  const footer = element('div', 'message-footer'); const summary = element('div', 'source-summary');
  const unique = new Map();
  (result.sources || []).forEach((s, i) => { const key = s.type === 'faq' ? s.id : (s.file_id || s.file_name); if (!unique.has(key)) unique.set(key, { source: s, index: i }); });
  unique.forEach(({ source, index }) => {
    const label = source.type === 'faq' ? '常见问答' : source.file_name;
    const b = makeButton(label, () => chooseSources(result.sources, view.question, index), 'source-chip', source.type === 'faq' ? 'chat' : 'file');
    summary.append(b);
  });
  if (result.sources?.length) summary.append(element('span', 'citation-total', `${result.sources.length} 条引用`));
  else summary.append(element('span', 'citation-total', result.status === 'greeting' ? '固定问候' : '未引用文档'));
  const copy = makeButton('', async () => {
    try { await navigator.clipboard.writeText(result.answer); notify('回答已复制'); }
    catch (_) { notify('无法访问剪贴板，请手动选择并复制。'); }
  }, 'icon-button', 'copy');
  copy.title = '复制回答'; copy.setAttribute('aria-label', '复制回答');
  footer.append(summary, copy); view.content.append(footer); view.footer = footer;
  if (select) chooseSources(result.sources || [], view.question, -1, false);
}
async function openSession(id) {
  if (busy) return;
  const version = ++navVersion; navigating = true; updateControls(); renderSessions();
  try {
    const data = await api(`/api/history/${encodeURIComponent(id)}`);
    if (version !== navVersion) return;
    sid = id; remember(); $('chatHistory').replaceChildren();
    if (!data.history.length) $('chatHistory').append(welcome);
    let latest = null;
    data.history.forEach(row => { message('user', row.question); message('assistant', row.answer, row, row.question); latest = row; });
    chooseSources(latest?.sources || [], latest?.question || '', -1, false);
    $('answerStatus').textContent = ''; $('answerStatus').className = '';
    scrollToLatest(true);
  } catch (error) {
    if (error.status === 404) { sessions = sessions.filter(s => s.id !== id); remember(); }
    throw error;
  } finally { if (version === navVersion) { navigating = false; updateControls(); renderSessions(); } }
}
async function newSession() {
  if (busy || navigating) return;
  navigating = true; updateControls();
  try {
    const data = await api('/api/create_session', { method: 'POST' });
    sessions.push({ id: data.session_id, title: '新会话' });
    navigating = false; showPage('chat'); await openSession(data.session_id); closeSidebar();
    $('chatInput').focus();
  } finally { navigating = false; updateControls(); }
}
function resizeInput() { $('chatInput').style.height = 'auto'; $('chatInput').style.height = `${Math.min(160, $('chatInput').scrollHeight)}px`; updateControls(); }
async function send() {
  const q = $('chatInput').value.trim();
  if (!q || busy || navigating || !initialized) return;
  if (!sid) await newSession();
  busy = true; updateControls(); renderSessions();
  $('chatInput').value = ''; resizeInput();
  const session = sessions.find(s => s.id === sid);
  if (session?.title === '新会话') session.title = q.slice(0, 36);
  remember(); message('user', q); const view = message('assistant', '', {}, q);
  chooseSources([], q, -1, false);
  $('answerStatus').textContent = '正在检索…'; $('answerStatus').className = '';
  const requestSid = sid; let answer = '', complete = false;
  const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/stream`);
  activeSocket = socket;
  const unlock = () => { busy = false; activeSocket = null; updateControls(); renderSessions(); };
  const fail = text => {
    if (complete) return;
    complete = true; finishMessage(view, { status: 'error', answer: text, sources: [] });
    const retry = makeButton('重新提问', () => { if (!busy) { $('chatInput').value = q; resizeInput(); $('chatInput').focus(); } }, 'subtle-button retry-button', 'refresh');
    view.content.append(retry);
    $('answerStatus').textContent = text; $('answerStatus').className = 'error';
    unlock(); socket.close(); scrollToLatest(true);
  };
  socket.onopen = () => socket.send(JSON.stringify({ query: q, session_id: requestSid,scope:Object.fromEntries([['region',$('queryRegion').value.trim()],['product',$('queryProduct').value.trim()]].filter(([,v])=>v)),as_of:$('queryDate').value || null }));
  socket.onmessage = event => {
    let data;
    try { data = JSON.parse(event.data); } catch (_) { fail('服务返回了无法读取的消息，请重试。'); return; }
    if (data.type === 'start') view.head.querySelector('.answer-label').textContent='生成中 / 待校验';
    if (data.type === 'status') $('answerStatus').textContent = data.message;
    if (data.type === 'token') {
      view.content.querySelector('.loading-step')?.remove(); answer += data.token;
      view.body.textContent = answer; scrollToLatest();
    }
    if (data.type === 'error') fail(data.error || '回答失败，请重试。');
    if (data.type === 'end') {
      if (data.validation_status === 'failed') data = {...data,status:'error',answer:'回答未通过最终校验，当前无法确认。',sources:[],citations:[],claims:[],conflicts:[]};
      complete = true; finishMessage(view, data); $('answerStatus').textContent = statuses[data.status] ?? '';
      unlock(); socket.close(); refreshStatus(); scrollToLatest();
    }
  };
  socket.onerror = () => fail('连接失败，请确认服务已启动后重试。');
  socket.onclose = () => { if (!complete) fail('连接已中断。可刷新当前会话，检查服务端是否已完成回答。'); };
}
function showPage(page) {
  currentPage = page; const knowledge = page === 'knowledge';
  $('knowledgeView').hidden = !knowledge; $('chatView').hidden = knowledge;
  $('chatNav').classList.toggle('active', !knowledge); $('kbBtn').classList.toggle('active', knowledge);
  $('chatNav').setAttribute('aria-current', knowledge ? 'false' : 'page'); $('kbBtn').setAttribute('aria-current', knowledge ? 'page' : 'false');
  $('sourcesToggle').hidden = knowledge; $('clearHistoryBtn').hidden = knowledge;
  $('threadTitle').textContent = knowledge ? '知识库管理' : (sessions.find(s => s.id === sid)?.title || '新会话');
  closeSidebar();
  if (knowledge) { loginView(); refreshStatus(); refreshFiles().catch(() => {}); }
}
function openSidebar() { $('sidebar').classList.add('open'); $('sidebarScrim').hidden = false; $('menuBtn').setAttribute('aria-expanded', 'true'); }
function closeSidebar() { $('sidebar').classList.remove('open'); $('sidebarScrim').hidden = true; $('menuBtn').setAttribute('aria-expanded', 'false'); }
async function refreshStatus() {
  if (statusRequest) return; statusRequest = true;
  try {
    const s = await api('/api/status');
    $('connectionBanner').hidden = true;
    $('statusDot').className = `status-dot${s.indexing_files || s.failed_files ? ' pending' : ''}`;
    $('knowledgeStatus').textContent = s.indexing_files ? '资料正在索引' : s.failed_files ? '部分资料需要处理' : (s.indexed_files || s.faq_count ? '知识库已就绪' : '知识库尚未导入资料');
    $('knowledgeCounts').textContent = `${s.indexed_files} 份文档 · ${s.faq_count} 条常见问答`;
    welcome.querySelector('#welcomeLibrary').textContent = s.indexed_files || s.faq_count ? '' : '尚未导入资料，请先在知识库管理中添加。';
    $('documentCount').textContent = s.indexed_files; $('faqCount').textContent = s.faq_count;
    $('indexingCount').textContent = s.indexing_files; $('failedCount').textContent = s.failed_files;
  } catch (error) {
    $('statusDot').className = 'status-dot offline'; $('knowledgeStatus').textContent = '暂时无法连接服务';
    $('knowledgeCounts').textContent = '连接恢复后会自动更新';
    $('connectionBanner').textContent = error.message; $('connectionBanner').hidden = false;
  } finally { statusRequest = false; }
}
function kbMessage(text, error = false) { $('kbMsg').textContent = text; $('kbMsg').className = `notice${error ? ' error' : ''}`; $('kbMsg').hidden = false; }
async function kbApi(path, options = {}) {
  try { return await api(path, { ...options, headers: { ...options.headers, Authorization: `Bearer ${token}` } }); }
  catch (error) { if (error.status === 401) forgetToken(); kbMessage(error.message, true); throw error; }
}
function loginView() { $('kbPanelBox').hidden = !token; $('kbLoginBox').hidden = Boolean(token); }
function confirmAction(title, description, label = '确认删除') {
  const dialog = $('confirmDialog');
  if (dialog.open) return Promise.resolve(false);
  $('confirmTitle').textContent = title; $('confirmDescription').textContent = description; $('confirmAction').textContent = label;
  dialog.returnValue = 'cancel'; dialog.showModal();
  return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true }));
}
function formatSize(size) { return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.ceil(size / 1024))} KB`; }
async function refreshFiles(force = false) {
  if (!token || fileRequest) return; fileRequest = true;
  try {
    const data = await kbApi('/api/kb/files');
    const signature = JSON.stringify(data.files); if (!force && signature === lastFileSignature) return;
    lastFileSignature = signature; $('kbFileList').replaceChildren(); $('filesEmpty').hidden = data.files.length > 0;
    const labels = { uploaded: '等待索引', indexing: '正在索引', ready: '可检索', failed: '索引失败' };
    data.files.forEach(file => {
      const row = element('tr'); const nameCell = element('td');
      const fileName = element('div', 'file-name-cell'); fileName.append(icon('file'), element('strong', '', file.file_name)); nameCell.append(fileName);
      row.append(nameCell, element('td', 'file-meta', formatSize(file.size)), element('td', 'file-meta', file.chunk_count));
      const stateCell = element('td'); const state = element('span', 'file-state'); state.dataset.state = file.status;
      state.append(element('span', '', '·'), document.createTextNode(labels[file.status] || file.status)); stateCell.append(state);
      if (file.error) stateCell.append(element('p', 'file-error', file.error)); row.append(stateCell);
      const actionsCell = element('td'); const actions = element('div', 'file-actions');
      if (['ready', 'failed'].includes(file.status)) {
        const rebuild = makeButton('', async () => {
          rebuild.disabled = true;
          try { await kbApi(`/api/kb/files/${file.id}/rebuild`, { method: 'POST' }); kbMessage(`已提交“${file.file_name}”的重建任务。`); await refreshFiles(true); await refreshStatus(); }
          finally { rebuild.disabled = false; }
        }, 'icon-button', 'refresh');
        rebuild.title = '重建索引'; rebuild.setAttribute('aria-label', `重建 ${file.file_name} 的索引`); actions.append(rebuild);
      }
      const remove = makeButton('', async () => {
        if (!await confirmAction('删除这份知识文档？', `“${file.file_name}”及其索引会被删除，之后不能再用于回答。历史证据记录保留，旧回答隐藏并要求重新查询。`)) return;
        remove.disabled = true;
        try { const result = await kbApi(`/api/kb/files/${file.id}`, { method: 'DELETE' }); kbMessage(result.warning || `已删除“${file.file_name}”及其索引。`); await refreshFiles(true); await refreshStatus(); }
        finally { remove.disabled = false; }
      }, 'icon-button', 'trash');
      remove.title = '删除文档'; remove.setAttribute('aria-label', `删除 ${file.file_name}`); actions.append(remove);
      actionsCell.append(actions); row.append(actionsCell); $('kbFileList').append(row);
    });
  } finally { fileRequest = false; }
}
function setFile(kind, file) {
  if (uploading[kind]) return;
  selectedFiles[kind] = file || null;
  $(kind === 'document' ? 'documentFileLabel' : 'faqFileLabel').textContent = file ? `${file.name} · ${formatSize(file.size)}` : (kind === 'document' ? '点击选择或拖入文档' : '点击选择或拖入 FAQ');
  $(kind === 'document' ? 'kbUploadBtn' : 'faqImportBtn').disabled = !file;
}
async function upload(kind) {
  const file = selectedFiles[kind]; if (!file || uploading[kind]) return;
  const button = $(kind === 'document' ? 'kbUploadBtn' : 'faqImportBtn');
  const input = $(kind === 'document' ? 'kbFile' : 'faqFile');
  uploading[kind] = true; button.disabled = true; input.disabled = true; button.textContent = '正在上传…';
  const form = new FormData(); form.append('file', file);
  try {
    const result = await kbApi(kind === 'document' ? '/api/kb/upload' : '/api/kb/faq/import', { method: 'POST', body: form });
    kbMessage(kind === 'document' ? `“${file.name}”上传完成，正在等待解析和索引。请在下方查看状态。` : `已导入 ${result.imported} 条常见问答。重复问题的答案已更新。`);
    input.value = ''; uploading[kind] = false; setFile(kind, null);
    await refreshFiles(true); await refreshStatus();
  } catch (_) { /* kbApi has an inline error */ }
  finally { uploading[kind] = false; input.disabled = false; button.textContent = kind === 'document' ? '上传文档' : '导入常见问答'; button.disabled = !selectedFiles[kind]; }
}
function setupDrop(kind, dropId, inputId) {
  const drop = $(dropId), input = $(inputId);
  input.addEventListener('change', () => setFile(kind, input.files[0]));
  drop.addEventListener('dragover', event => { event.preventDefault(); drop.classList.add('dragover'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
  drop.addEventListener('drop', event => {
    event.preventDefault(); drop.classList.remove('dragover');
    if (event.dataTransfer.files.length !== 1) { kbMessage('每次请选择一份文件。', true); return; }
    setFile(kind, event.dataTransfer.files[0]);
  });
}

$('sendBtn').onclick = () => send().catch(error => { busy = false; updateControls(); notify(error.message); });
$('chatInput').oninput = resizeInput;
$('chatInput').onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('sendBtn').click(); } };
$('newSessionBtn').onclick = () => newSession().catch(error => notify(error.message));
$('clearHistoryBtn').onclick = async () => {
  if (!sid || busy || navigating) return;
  if (!await confirmAction('清空当前会话？', '当前会话中的所有问题和回答将被删除。其他会话不受影响。', '清空会话')) return;
  try { await api(`/api/history/${sid}`, { method: 'DELETE' }); await openSession(sid); notify('当前会话已清空'); }
  catch (error) { notify(error.message); }
};
$('searchBox').oninput = renderSessions;
$('sourcesToggle').onclick = () => setSourcePanel($('evidencePanel').hidden);
$('sourcesClose').onclick = () => { setSourcePanel(false); $('sourcesToggle').focus(); };
$('kbBtn').onclick = () => showPage('knowledge');
$('chatNav').onclick = $('backToChat').onclick = () => showPage('chat');
$('brandLink').onclick = event => { event.preventDefault(); showPage('chat'); };
$('menuBtn').onclick = openSidebar; $('sidebarScrim').onclick = closeSidebar;
$('themeToggle').onclick = () => { document.body.classList.toggle('dark-mode'); safeSet(localStorage, 'knowledgeAssistantDark', document.body.classList.contains('dark-mode')); };
$('loginForm').onsubmit = async event => {
  event.preventDefault(); $('kbLoginBtn').disabled = true;
  try {
    const data = await api('/api/kb/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: $('kbUsername').value, password: $('kbPassword').value }) });
    token = data.token; safeSet(sessionStorage, storageKey('Admin'), token); $('kbPassword').value = '';
    loginView(); kbMessage('登录成功，可以开始管理资料。'); await refreshFiles(true);
  } catch (error) { kbMessage(error.message, true); }
  finally { $('kbLoginBtn').disabled = false; }
};
$('kbLogoutBtn').onclick = async () => { try { await kbApi('/api/kb/logout', { method: 'POST' }); } catch (_) { /* expired token also clears locally */ } finally { forgetToken(); lastFileSignature = ''; kbMessage('已退出管理员登录。'); } };
$('kbRefreshBtn').onclick = () => refreshFiles(true).catch(() => {});
$('kbUploadBtn').onclick = () => upload('document'); $('faqImportBtn').onclick = () => upload('faq');
setupDrop('document', 'documentDrop', 'kbFile'); setupDrop('faq', 'faqDrop', 'faqFile');
$('downloadFaqExample').onclick = () => {
  const content = JSON.stringify([{ question: '示例问题，请替换为你的内容', answer: '示例答案，请依据你的资料填写' }], null, 2);
  const url = URL.createObjectURL(new Blob([content], { type: 'application/json;charset=utf-8' }));
  const link = element('a'); link.href = url; link.download = 'faq-template.json'; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') { if (!$('confirmDialog').open) { closeSidebar(); setSourcePanel(false); } }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); if (innerWidth <= 760) openSidebar(); $('searchBox').focus(); }
  const editing = /INPUT|TEXTAREA|SELECT/.test(event.target.tagName) || event.target.isContentEditable;
  if (!editing && !event.metaKey && !event.ctrlKey && event.key.toLowerCase() === 'n' && !$('confirmDialog').open) { event.preventDefault(); $('newSessionBtn').click(); }
});
async function initialize() {
  try {
    settings = await api('/api/config'); storagePrefix = `knowledgeAssistant:${settings.instance_id}`;
    try { const data = JSON.parse(safeGet(localStorage, storageKey('Sessions')) || '[]'); sessions = Array.isArray(data) ? data.filter(s => typeof s.id === 'string' && typeof s.title === 'string') : []; } catch (_) { sessions = []; }
    token = safeGet(sessionStorage, storageKey('Admin')) || '';
    document.title = settings.name; $('assistantName').textContent = settings.name; $('chatInput').setAttribute('aria-description', settings.welcome || settings.description || '');
    document.body.classList.toggle('dark-mode', safeGet(localStorage, 'knowledgeAssistantDark') === 'true');
    (settings.suggestions || []).forEach(q => $('quickReplies').append(makeButton(q, () => { if (busy) return; $('chatInput').value = q; resizeInput(); $('chatInput').focus(); }, 'quick-reply-btn')));
    initialized = true; updateControls(); renderEvidence();
    const previous = safeGet(localStorage, storageKey('Current'));
    if (previous) {
      try { await openSession(previous); }
      catch (error) { if (error.status === 404) await newSession(); else throw error; }
    } else await newSession();
    await refreshStatus();
  } catch (error) {
    $('answerStatus').textContent = `初始化失败：${error.message} 刷新页面可重试。`;
    $('answerStatus').className = 'error'; $('connectionBanner').textContent = error.message; $('connectionBanner').hidden = false;
    updateControls();
  }
}
initialize();
setInterval(() => { if (document.hidden) return; refreshStatus(); if (currentPage === 'knowledge' && token) refreshFiles().catch(() => {}); }, 5000);


$('reviewRefresh').addEventListener('click',()=>loadReviews().catch(e=>notify(e.message)));
async function loadReviews() {
  const data=await kbApi('/api/kb/evidence');const list=$('reviewRecords');list.replaceChildren();
  data.versions.forEach(v=>{
    const box=element('details','review-record');box.append(element('summary','',`${v.file_name} · 版本 ${v.document_version.slice(0,12)}`));
    const editor=element('textarea','policy-editor');editor.value=JSON.stringify(v.policy,null,2);editor.setAttribute('aria-label','证据元数据 JSON');
    const reason=element('input');reason.placeholder='修正依据（必填）';reason.setAttribute('aria-label','修正依据');
    box.append(editor,reason,makeButton('保存元数据',async()=>{await kbApi(`/api/kb/evidence/versions/${v.version_id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({policy:JSON.parse(editor.value),reason:reason.value})});notify('已保存，历史回答失效');await loadReviews();}),
      makeButton('撤销最近修正',async()=>{await kbApi(`/api/kb/evidence/versions/${v.version_id}/undo`,{method:'POST'});await loadReviews();}));
    appendAudit(box,v.version_id);list.append(box);
  });
  data.conflicts.forEach(g=>{
    const box=element('section','conflict-detail');box.append(element('h3','','冲突复核'),element('p','',g.reason));
    const basis=element('input');basis.placeholder='裁决依据（必填）';basis.setAttribute('aria-label','裁决依据');box.append(basis);
    g.statements.forEach(c=>box.append(element('p','',c.text),makeButton('采用这项说法',async()=>{await kbApi(`/api/kb/evidence/conflicts/${g.conflict_group}/decision`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chosen_claim_id:c.claim_id,basis:basis.value})});notify('已记录裁决，请重新查询');await loadReviews();})));
    const decision=data.decisions.find(d=>d.conflict_group===g.conflict_group);if(decision)box.append(element('p','',`裁决${decision.revoked?'已撤销':'有效'}：${decision.basis}`));
    box.append(makeButton('撤销裁决',async()=>{await kbApi(`/api/kb/evidence/conflicts/${g.conflict_group}/decision`,{method:'DELETE'});await loadReviews();}));appendAudit(box,g.conflict_group);list.append(box);
  });
}
function appendAudit(box,id) {
  const audit=element('pre','audit-record');box.append(makeButton('查看操作记录',async()=>{const d=await kbApi(`/api/kb/evidence/audit/${id}`);audit.textContent=d.events.map(e=>`${e.created_at} · ${e.actor} · ${e.reason}`).join('\n') || '暂无记录';}),audit);
}
