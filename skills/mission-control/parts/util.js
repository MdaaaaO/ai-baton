// Helpers shared by the section renderers. Store text reaches the page only through text nodes and
// setAttribute, never as markup, so a title holding angle brackets shows literally.
const $ = (id) => document.getElementById(id);

function put(node, kid) {
  if (kid === null || kid === undefined || kid === false) return node;
  if (Array.isArray(kid)) {
    kid.forEach((k) => put(node, k));
  } else {
    node.appendChild(typeof kid === 'object' ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  Object.keys(props || {}).forEach((key) => {
    const v = props[key];
    if (v === null || v === undefined || v === false) return;
    if (key === 'class') node.className = v;
    else node.setAttribute(key, v === true ? '' : String(v));
  });
  kids.forEach((kid) => put(node, kid));
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

const empty = (msg) => el('p', { class: 'empty' }, msg);
const badge = (text, kind) => el('span', { class: kind ? 'badge ' + kind : 'badge' }, text);
const present = (v) => v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && v.length === 0);
const list = (v) => (Array.isArray(v) ? v : []);

// Only http(s) targets become links; anything else is shown as plain text.
function link(href, label) {
  if (typeof href === 'string' && /^https?:\/\//i.test(href)) {
    return el('a', { href: href, rel: 'noopener', target: '_blank' }, label);
  }
  return el('span', null, label);
}

// ── numbers ────────────────────────────────────────────────────────────────────────────────────────────
const isNum = (n) => typeof n === 'number' && isFinite(n);
const fmt = (n, max) => (isNum(n) ? n.toLocaleString('en-US', { maximumFractionDigits: max === undefined ? 2 : max }) : '—');
const usd = (n) => (isNum(n) ? '~$' + fmt(n, 2) : '—');

function size(n) {
  if (!isNum(n)) return '—';
  if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
  return n >= 1024 ? (n / 1024).toFixed(1) + ' KB' : n + ' B';
}

// ── time: the display zone is data.tz, UTC always beside it ───────────────────────────────────────────
const STAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
const parseStamp = (s) => (typeof s === 'string' && STAMP.test(s) ? Date.parse(s) : NaN);

let zoneFmt = null;

function setZone(zone) {
  const opts = { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
                 hourCycle: 'h23', timeZoneName: 'short' };
  zoneFmt = null;
  if (zone) {
    try {
      zoneFmt = new Intl.DateTimeFormat('en-CA', Object.assign({ timeZone: zone }, opts));
    } catch (e) {
      zoneFmt = null; // not a zone this browser knows: fall back to its own
    }
  }
  if (!zoneFmt) zoneFmt = new Intl.DateTimeFormat('en-CA', opts);
}

function zoneParts(ms) {
  const parts = {};
  zoneFmt.formatToParts(new Date(ms)).forEach((p) => { parts[p.type] = p.value; });
  return parts;
}

function inZone(ms) {
  const p = zoneParts(ms);
  return p.year + '-' + p.month + '-' + p.day + ' ' + p.hour + ':' + p.minute + ' ' + p.timeZoneName;
}

function clockInZone(ms) {
  const p = zoneParts(ms);
  return p.hour + ':' + p.minute + ' ' + p.timeZoneName;
}

const inUtc = (ms) => new Date(ms).toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
const both = (ms) => inZone(ms) + ' · ' + inUtc(ms);

function ago(ms, now) {
  const s = Math.max(0, (now - ms) / 1000);
  if (s < 90) return 'just now';
  if (s < 5400) return Math.round(s / 60) + ' min ago';
  if (s < 129600) return Math.round(s / 3600) + ' h ago';
  return Math.round(s / 86400) + ' d ago';
}

// A heartbeat is stale once it is older than stale_hours when the page is opened (the same rule the
// generator applies; the data file stores no age, so it never goes out of date on its own).
function isStale(heartbeat, staleHours, now) {
  const ms = parseStamp(heartbeat);
  return !isNaN(ms) && now - ms > (Number(staleHours) || 12) * 3600e3;
}

const CLOCK_UTC = /\b([01]?\d|2[0-3]):([0-5]\d)\s*UTC\b/g;
const ISO_Z = /\b(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?Z\b/g;

function scan(re, text, each) {
  re.lastIndex = 0;
  let m = re.exec(text);
  while (m !== null) {
    each(m);
    m = re.exec(text);
  }
}

// Every "HH:MM UTC" and ISO "...Z" time in a line of free text, as the display zone's reading + the UTC one.
function timesIn(line, now) {
  const out = [];
  const today = new Date(now);
  scan(ISO_Z, line, (m) => {
    const ms = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
    if (!isNaN(ms)) out.push(both(ms));
  });
  scan(CLOCK_UTC, line, (m) => {
    const ms = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate(), +m[1], +m[2]);
    out.push(clockInZone(ms) + ' · ' + String(m[1]).padStart(2, '0') + ':' + m[2] + ' UTC');
  });
  return out;
}

// ── clipboard ──────────────────────────────────────────────────────────────────────────────────────────
function copyFallback(text) {
  const area = el('textarea', { class: 'sink', readonly: true, tabindex: '-1', 'aria-hidden': 'true' });
  area.value = text;
  document.body.appendChild(area);
  area.select();
  area.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch (e) {
    ok = false;
  }
  document.body.removeChild(area);
  return ok;
}

function copyText(text) {
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      return navigator.clipboard.writeText(text).then(() => true, () => copyFallback(text));
    }
  } catch (e) {
    // fall through to the textarea route
  }
  return Promise.resolve(copyFallback(text));
}

// One small button: copies `text`, then swaps its own label to "copied" (or "copy failed") for a moment.
function copyButton(text, label, kind) {
  const value = String(text);
  const idle = label || 'copy';
  const btn = el('button', { type: 'button', class: kind || 'copy', title: 'copy: ' + value.slice(0, 120), 'aria-live': 'polite' }, idle);
  btn.addEventListener('click', () => {
    copyText(value).then((ok) => ok, () => false).then((ok) => {
      btn.textContent = ok ? 'copied' : 'copy failed';
      clearTimeout(btn.resetTimer);
      btn.resetTimer = setTimeout(() => { btn.textContent = idle; }, 1500);
    });
  });
  return btn;
}

// A command (or path) in code font with its copy button.
const cmd = (text) => el('span', { class: 'cmd' }, el('code', null, text), copyButton(text));
