// The six section renderers. Each one fills the body element it is given from the parsed data file, using
// elements and text nodes only, and shows a plain line when its dataset is empty.
const NOW = Date.now();
const FIND = 'make -C $BATON/context-db find ';
const VIEW = 'make -C $BATON/context-db view DOC=';
const LEDGER_CAP = 40;
const DOC_CAP = 100;

const muted = (text) => el('span', { class: 'muted' }, text);
const heading = (text, extra) => el('h3', null, text, present(extra) ? el('span', { class: 'when' }, extra) : null);
const epicNode = (epic, url) => (present(epic) ? (present(url) ? link(url, epic) : epic) : null);
const counts = (obj) => Object.keys(obj || {}).sort().map((k) => k + ' ' + obj[k]).join(' · ');

// "<zone reading> · <UTC reading>", plus how long ago for live rows; unparseable text is shown as it is.
function stampText(stamp, withAgo) {
  const ms = parseStamp(stamp);
  if (isNaN(ms)) return present(stamp) ? String(stamp) : '—';
  return both(ms) + (withAgo ? ' (' + ago(ms, NOW) + ')' : '');
}

// A label/value grid; a row whose value is empty is left out.
function facts(rows) {
  const dl = el('dl', { class: 'facts' });
  rows.forEach((row) => {
    if (!present(row[1]) || row[1] === false) return;
    dl.appendChild(el('div', null, el('dt', null, row[0]), el('dd', null, row[1])));
  });
  return dl;
}

// cols: [{label, num}], rows: arrays of cells (text or elements). Wide tables scroll inside their own box.
function table(cols, rows) {
  const head = el('tr', null, cols.map((c) => el('th', { scope: 'col', class: c.num ? 'num' : null }, c.label)));
  const lines = rows.map((r) => el('tr', null, r.map((cell, i) => el('td', { class: cols[i].num ? 'num' : null }, cell))));
  return el('div', { class: 'scroll' }, el('table', null, el('thead', null, head), el('tbody', null, lines)));
}

function problemList(problems) {
  const items = list(problems);
  return items.length ? el('ul', { class: 'problems' }, items.map((p) => el('li', null, String(p)))) : null;
}

const tile = (value, label, hint) => el('div', { class: 'tile', title: hint }, el('b', null, value), el('span', null, label));

// ── sessions ───────────────────────────────────────────────────────────────────────────────────────────
function cronList(lines) {
  return el('ul', { class: 'crons' }, list(lines).map((line) => {
    const text = String(line);
    const times = timesIn(text, NOW);
    return el('li', null, el('span', { class: 'mono' }, text),
      times.length ? el('span', { class: 'when' }, '→ ' + times.join(' ; ')) : null);
  }));
}

const prList = (prs) => el('ul', { class: 'plain' }, list(prs).map((pr) => el('li', null, link(pr.url, pr.text || (pr.repo + '#' + pr.number)))));
const usage = (s) => (present(s.stats) ? s.stats : (isNum(s.cost_usd) ? usd(s.cost_usd) : null));

function sessionCard(s, data, ended) {
  const stale = !ended && isStale(s.heartbeat, data.stale_hours, NOW);
  const head = el('header', null, el('span', { class: 'name' }, s.name || '(unnamed)'));
  if (stale) {
    head.appendChild(el('span', { class: 'badge stale', title: 'no heartbeat for more than ' + (data.stale_hours || 12) + ' h' }, 'STALE'));
  }
  const rows = [
    ['working on', s.working_on],
    ['heartbeat', stampText(s.heartbeat, true)],
    ['epic', epicNode(s.epic, s.epic_url)],
    ['repos', list(s.repos).join(', ')],
    ['responsibilities', s.responsibilities],
    ['usage', usage(s)],
    ['registry', present(s.path) ? cmd(s.path) : null],
  ];
  if (ended) {
    rows.push(['prompt', s.has_prompt ? copyButton(s.prompt, 'copy prompt') : muted('no prompt saved')]);
  } else {
    rows.push(['crons', list(s.crons).length ? cronList(s.crons) : null]);
    rows.push(['open PRs', list(s.open_prs).length ? prList(s.open_prs) : null]);
  }
  return el('div', { class: 'card' }, head, facts(rows));
}

function renderSessions(body, data) {
  const s = data.sessions || {};
  const active = list(s.active);
  const ended = list(s.ended);
  const c = s.counts || {};
  const archived = isNum(c.archived) ? c.archived : list(s.archived).length;
  if (!active.length && !ended.length && !archived) {
    put(body, empty('none — no sessions registered'));
    put(body, problemList(s.problems));
    return;
  }
  put(body, el('p', { class: 'summary' }, active.length + ' active · ' + ended.length + ' ended · ' + archived + ' archived'));
  put(body, problemList(s.problems));
  put(body, heading('Active', active.length));
  put(body, active.length ? active.map((row) => sessionCard(row, data, false)) : empty('none'));
  put(body, heading('Ended', ended.length + (c.ended_no_prompt ? ' · ' + c.ended_no_prompt + ' without a saved prompt' : '')));
  put(body, ended.length ? ended.map((row) => sessionCard(row, data, true)) : empty('none'));
  put(body, el('p', { class: 'muted' }, 'Archived: ' + archived + ' (not listed here)'));
}

// ── cost ───────────────────────────────────────────────────────────────────────────────────────────────
const LEDGER_COLS = [
  { label: 'Ended' }, { label: 'Session' }, { label: 'Epic' },
  { label: 'Hours', num: true }, { label: 'Turns', num: true }, { label: 'PRs', num: true }, { label: 'Spend', num: true },
];

function ledgerRow(r) {
  const ms = parseStamp(r.ended_utc);
  return [isNaN(ms) ? (r.ended || '—') : both(ms), r.session || '—', epicNode(r.epic, r.epic_url) || muted('no epic'),
    fmt(r.hours, 1), fmt(r.turns, 0), fmt(r.prs, 0), el('span', { title: r.usd_split ? 'split ' + r.usd_split : null }, usd(r.usd))];
}

function newestFirst(rows) {
  const keyed = rows.map((row, i) => ({ row: row, i: i, ms: parseStamp(row.ended_utc) }));
  keyed.sort((a, b) => {
    if (isNaN(a.ms) !== isNaN(b.ms)) return isNaN(a.ms) ? 1 : -1;
    return (isNaN(a.ms) ? 0 : b.ms - a.ms) || a.i - b.i;
  });
  return keyed.map((k) => k.row);
}

// The newest rows first; the rest sit behind one button so a long ledger does not bury the page.
function drawLedger(host, rows, all) {
  clear(host);
  put(host, table(LEDGER_COLS, (all ? rows : rows.slice(0, LEDGER_CAP)).map(ledgerRow)));
  if (!all && rows.length > LEDGER_CAP) {
    const more = el('button', { type: 'button', class: 'chip' }, 'show all ' + rows.length);
    more.addEventListener('click', () => drawLedger(host, rows, true));
    put(host, el('p', { class: 'muted' }, 'Newest ' + LEDGER_CAP + ' of ' + rows.length + ' rows. ', more));
  }
}

function renderCost(body, data) {
  const c = data.cost || {};
  const t = c.totals || {};
  const w7 = t.last_7d || {};
  const w30 = t.last_30d || {};
  const ledger = list(c.ledger);
  const live = list(c.live);
  put(body, present(c.basis) ? el('p', { class: 'muted' }, 'Basis: ' + c.basis) : null);
  if (!ledger.length && !live.length) {
    put(body, empty('none — no ledger rows and no live sessions'));
    return;
  }
  put(body, el('div', { class: 'tiles' }, [
    tile(usd(t.usd), 'total'),
    tile(usd(w7.usd), 'last 7 d · ' + fmt(w7.rows, 0) + ' rows', 'counted back from the data-as-of time'),
    tile(usd(w30.usd), 'last 30 d · ' + fmt(w30.rows, 0) + ' rows', 'counted back from the data-as-of time'),
    tile(fmt(t.rows, 0), 'ledger rows'),
    tile(fmt(t.hours, 1), 'hours'),
    tile(fmt(t.turns, 0), 'turns'),
  ]));
  const epics = list(t.by_epic);
  put(body, heading('By epic'));
  put(body, epics.length
    ? table([{ label: 'Epic' }, { label: 'Rows', num: true }, { label: 'Spend', num: true }],
      epics.map((e) => [epicNode(e.epic, e.epic_url) || muted('no epic'), fmt(e.rows, 0), usd(e.usd)]))
    : empty('none'));
  put(body, heading('Ledger', ledger.length));
  const host = el('div');
  put(body, host);
  if (ledger.length) drawLedger(host, newestFirst(ledger), false);
  else put(host, empty('none'));
  put(body, heading('Live sessions', live.length));
  put(body, live.length
    ? table([{ label: 'Session' }, { label: 'Usage' }, { label: 'Spend', num: true }],
      live.map((s) => [s.name || '—', s.stats || '—', usd(s.cost_usd)]))
    : empty('none'));
}

// ── catalog ────────────────────────────────────────────────────────────────────────────────────────────
function docCard(d) {
  const meta = el('div', { class: 'meta' },
    badge(d.type || '?'), badge(d.status || '?'), muted(d.domain || '?'),
    present(d.updated) ? muted('updated ' + d.updated) : null,
    d.large
      ? el('span', { class: 'badge warn', title: 'large doc: loading it whole is expensive, read the part you need' }, 'large · ' + size(d.bytes))
      : muted(size(d.bytes)),
    copyButton(VIEW + d.path, 'copy view'));
  const tags = list(d.tags).length
    ? el('div', { class: 'tags' }, list(d.tags).map((tag) => copyButton(FIND + 'TAG=' + tag, tag, 'chip')))
    : null;
  return el('div', { class: 'doc' }, el('span', { class: 'title' }, d.title || d.path), el('code', { class: 'path' }, d.path), meta, tags);
}

const domainRow = (d) => el('div', { class: 'dom' }, el('b', null, d.domain || '?'),
  muted(fmt(d.docs, 0) + ' docs · ' + size(d.bytes)), copyButton(FIND + 'DOMAIN=' + (d.domain || '?'), 'copy find', 'chip'));

function renderCatalog(body, data) {
  const c = data.catalog || {};
  const docs = list(c.docs);
  const n = c.counts || {};
  put(body, problemList(c.problems));
  if (!docs.length) {
    put(body, empty('none — no documents in the store'));
    return;
  }
  put(body, el('p', { class: 'summary' }, docs.length + ' documents'));
  put(body, el('p', { class: 'muted' }, 'status: ' + counts(n.by_status) + ' — type: ' + counts(n.by_type)));
  put(body, heading('Domains', list(c.domains).length));
  put(body, el('div', { class: 'domains' }, list(c.domains).map(domainRow)));
  const input = el('input', { type: 'search', class: 'search', placeholder: 'path, title, domain, type, tag', 'aria-label': 'filter documents' });
  const note = el('p', { class: 'muted', 'aria-live': 'polite' });
  const results = el('div');
  const hay = docs.map((d) => [d.path, d.title, d.domain, d.type].concat(list(d.tags)).join(' ').toLowerCase());
  const apply = () => {
    const terms = input.value.toLowerCase().split(/\s+/).filter(Boolean);
    const hits = docs.filter((d, i) => terms.every((term) => hay[i].indexOf(term) !== -1));
    clear(results);
    put(results, hits.slice(0, DOC_CAP).map(docCard));
    if (!hits.length) put(results, empty('no document matches'));
    note.textContent = hits.length + ' of ' + docs.length + ' documents' + (hits.length > DOC_CAP ? ' — showing the first ' + DOC_CAP + ', narrow the filter for the rest' : '');
  };
  input.addEventListener('input', apply);
  put(body, heading('Documents'));
  put(body, [input, note, results]);
  apply();
}

// ── priorities ─────────────────────────────────────────────────────────────────────────────────────────
function checkRow(item) {
  return el('label', { class: item.done ? 'check done' : 'check' },
    el('input', { type: 'checkbox', disabled: true, checked: !!item.done }), el('span', null, item.text || ''));
}

function renderPriorities(body, data) {
  const p = data.priorities || {};
  if (!p.present) {
    put(body, empty('no priorities file'));
    return;
  }
  const items = list(p.items);
  put(body, el('p', { class: 'muted' }, 'Read-only view of ', cmd(VIEW + p.path)));
  if (!items.length) {
    put(body, empty('none — no checklist items in the priorities file'));
    return;
  }
  const groups = [];
  items.forEach((item) => {
    const name = item.section || '';
    let group = groups.find((g) => g.name === name);
    if (!group) {
      group = { name: name, items: [] };
      groups.push(group);
    }
    group.items.push(item);
  });
  groups.forEach((g) => {
    put(body, heading(g.name || 'No heading', g.items.filter((i) => i.done).length + '/' + g.items.length + ' done'));
    put(body, g.items.map(checkRow));
  });
}

// ── queues ─────────────────────────────────────────────────────────────────────────────────────────────
function renderQueues(body, data) {
  const q = data.queues || {};
  const pr = q.pr_review || {};
  const last = list(pr.last);
  put(body, heading('pr-review ledger', fmt(pr.rows, 0) + ' rows'));
  if (!last.length) {
    put(body, empty('none — no pr-review rows'));
  } else {
    const by = pr.by_status || {};
    put(body, el('p', null, Object.keys(by).sort().map((k) => [badge(k + ' ' + by[k]), ' '])));
    put(body, table([{ label: 'When' }, { label: 'PR' }, { label: 'Head' }, { label: 'Status' }],
      last.map((r) => [stampText(r.ts), isNum(r.pr) ? link(r.url, (r.repo || '') + '#' + r.pr) : '—', r.head || '—', r.status || '—'])));
  }
  const jobs = list((q.sign_queue || {}).jobs);
  put(body, heading('sign-queue', jobs.length + ' jobs'));
  put(body, jobs.length
    ? table([{ label: 'Job' }, { label: 'Queued' }], jobs.map((j) => [j.name, stampText(j.mtime_utc)]))
    : empty('none — no queued jobs'));
  const kh = q.kit_health || {};
  put(body, heading('kit-health'));
  if (!kh.latest && !kh.stamp) {
    put(body, empty('none — no walk logged'));
    return;
  }
  put(body, facts([
    ['latest walk', kh.latest ? kh.latest.date : null],
    ['log', kh.latest ? cmd(VIEW + kh.latest.path) : null],
    ['health stamp', kh.stamp ? 'updated ' + kh.stamp.updated : null],
    ['stamp doc', kh.stamp ? cmd(VIEW + kh.stamp.path) : null],
  ]));
  put(body, kh.latest && present(kh.latest.outcome) ? el('div', { class: 'outcome' }, kh.latest.outcome) : null);
}

// ── pointers ───────────────────────────────────────────────────────────────────────────────────────────
function renderPointers(body, data) {
  const p = data.pointers || {};
  const memory = list(p.memory);
  put(body, heading('Memory pointers', memory.length));
  put(body, memory.length
    ? el('ul', { class: 'plain' }, memory.map((m) => el('li', null, el('b', null, m.title || m.file),
      present(m.hook) ? ' — ' + m.hook : null, ' ', el('code', { class: 'muted' }, m.file))))
    : empty('none — no memory index'));
  const idx = p.index;
  const sidx = p.session_index;
  put(body, heading('Generated indexes'));
  put(body, facts([
    ['INDEX.md', idx ? fmt(idx.docs, 0) + ' docs · generated ' + idx.generated : muted('not found')],
    ['SESSION_INDEX.md', sidx
      ? [fmt(sidx.active, 0) + ' active', fmt(sidx.ended_listed, 0) + ' ended listed', fmt(sidx.ended_no_prompt, 0) + ' without a prompt'].join(' · ') + ' · generated ' + sidx.generated
      : muted('not found')],
  ]));
  const sources = list(p.sources);
  put(body, heading('Sources read', sources.length));
  put(body, sources.length
    ? table([{ label: 'Source' }, { label: 'Path' }, { label: 'Found' }],
      sources.map((s) => [s.name, el('code', null, s.path), s.present ? badge('present', 'ok') : badge('missing', 'warn')]))
    : empty('none'));
}

const RENDER = {
  sessions: renderSessions, cost: renderCost, catalog: renderCatalog,
  priorities: renderPriorities, queues: renderQueues, pointers: renderPointers,
};
