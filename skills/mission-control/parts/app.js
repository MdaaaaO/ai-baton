// Boot: read the two JSON blocks, fill the time lines, draw the six sections and wire the tabs.
const TABS = ['sessions', 'cost', 'catalog', 'priorities', 'queues', 'pointers'];
const TAB_KEY = 'mission.tab';

function readBlock(id) {
  const node = $(id);
  if (!node) return null;
  try {
    const value = JSON.parse(node.textContent);
    return value && typeof value === 'object' ? value : null;
  } catch (e) {
    return null;
  }
}

function fillTimes(data, build) {
  const none = data.as_of === null || data.as_of === undefined || data.as_of === '';
  const asOf = parseStamp(data.as_of);
  const asOfText = none ? '— (no source timestamp)' : (isNaN(asOf) ? String(data.as_of) : both(asOf));
  const made = parseStamp(build.generated_at);
  const builtText = 'page built ' + (isNaN(made) ? '—' : both(made)) + ' · kit ' + (present(build.kit_version) ? build.kit_version : '—');
  document.querySelectorAll('[data-asof]').forEach((node) => { node.textContent = asOfText; });
  document.querySelectorAll('[data-built]').forEach((node) => { node.textContent = builtText; });
}

function draw(id, data) {
  const body = $(id).querySelector('.body');
  clear(body);
  try {
    RENDER[id](body, data);
  } catch (e) {
    clear(body);
    body.appendChild(el('p', { class: 'problems' }, 'This section could not be drawn: ' + (e && e.message ? e.message : e)));
  }
}

// The tab comes from a bare location.hash token, else the one remembered last time, else the first.
function currentTab() {
  const wanted = location.hash.replace(/^#/, '');
  if (TABS.indexOf(wanted) !== -1) return wanted;
  let stored = null;
  try {
    stored = localStorage.getItem(TAB_KEY);
  } catch (e) {
    stored = null;
  }
  return TABS.indexOf(stored) !== -1 ? stored : TABS[0];
}

function showTab(id) {
  TABS.forEach((tab) => {
    $(tab).hidden = tab !== id;
    const anchor = $('tab-' + tab);
    if (tab === id) anchor.setAttribute('aria-current', 'page');
    else anchor.removeAttribute('aria-current');
  });
  try {
    localStorage.setItem(TAB_KEY, id);
  } catch (e) {
    // storage can be blocked; the tab still switches
  }
  window.scrollTo(0, 0);
}

function boot() {
  const data = readBlock('data');
  const build = readBlock('build') || {};
  if (data) {
    setZone(data.tz);
    fillTimes(data, build);
    TABS.forEach((id) => draw(id, data));
  } else {
    TABS.forEach((id) => {
      put($(id).querySelector('.body'), el('p', { class: 'problems' }, 'The data block is missing or unreadable; rebuild the page.'));
    });
  }
  window.addEventListener('hashchange', () => showTab(currentTab()));
  // The browser scrolls to a #fragment once the page has loaded; start from the top instead.
  window.addEventListener('load', () => setTimeout(() => window.scrollTo(0, 0), 0));
  showTab(currentTab());
}

boot();
