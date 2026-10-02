// Background worker:
//  1. forwards copied BetMGM odds JSON to the local scanner (http://127.0.0.1:<port>/ingest/betmgm)
//  2. OPTIONAL auto-reload (off by default): reloads the BetMGM tabs you marked, every N minutes, one at a
//     time, skipping the tab you're currently looking at. Same as you pressing reload; nothing else.

const DEFAULTS = { port: 8000, autoReload: false, intervalMin: 5, markedTabs: [] };
const ALARM = "evc-reload";
const GAP_BETWEEN_RELOADS_MS = 20000;

async function settings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
}

async function record(update) {
  const s = (await chrome.storage.local.get("stats")).stats || { sent: 0, failed: 0 };
  await chrome.storage.local.set({ stats: { ...s, ...update(s) } });
}

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (!sender.tab) return;
  if (msg?.type === "hello") {  // tap script is running in a BetMGM tab
    record(() => ({ lastHello: Date.now(), lastHelloPath: msg.url }));
    return;
  }
  if (msg?.type !== "payload") return;
  (async () => {
    await record((s) => ({ captured: (s.captured || 0) + 1, lastCaptured: Date.now() }));
    const { port } = await settings();
    try {
      const r = await fetch(`http://127.0.0.1:${port}/ingest/betmgm`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-evc-tap": "betmgm" },
        body: JSON.stringify({ path: msg.path, data: msg.data }),
      });
      const detail = r.ok ? "" : await r.text().catch(() => "");
      await record((s) => r.ok
        ? { sent: s.sent + 1, lastSent: Date.now(), lastPath: msg.path, lastError: null }
        : { failed: s.failed + 1, lastError: `scanner rejected it (${r.status}) ${detail.slice(0, 80)}` });
    } catch (e) {
      await record((s) => ({ failed: s.failed + 1, lastError: `can't reach scanner on port ${port}: ${e.message}` }));
    }
  })();
});

async function scheduleAlarm() {
  const { autoReload, intervalMin } = await settings();
  await chrome.alarms.clear(ALARM);
  if (autoReload) chrome.alarms.create(ALARM, { periodInMinutes: Math.max(1, Number(intervalMin) || 5) });
}

chrome.runtime.onInstalled.addListener(scheduleAlarm);
chrome.runtime.onStartup.addListener(scheduleAlarm);
chrome.storage.onChanged.addListener((changes) => {
  if (changes.autoReload || changes.intervalMin) scheduleAlarm();
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== ALARM) return;
  const { autoReload, markedTabs } = await settings();
  if (!autoReload) return;
  const [focused] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  const alive = [];
  for (const id of markedTabs) {
    let tab;
    try { tab = await chrome.tabs.get(id); } catch { continue; }  // closed: drop it
    alive.push(id);
    if (!tab.url?.startsWith("https://www.pa.betmgm.com/")) continue;
    if (focused && focused.id === id) continue;  // never reload what you're looking at
    await chrome.tabs.reload(id);
    await new Promise((r) => setTimeout(r, GAP_BETWEEN_RELOADS_MS));
  }
  if (alive.length !== markedTabs.length) await chrome.storage.local.set({ markedTabs: alive });
});
