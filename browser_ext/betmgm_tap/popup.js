const $ = (id) => document.getElementById(id);
const DEFAULTS = { port: 8000, autoReload: false, intervalMin: 5, markedTabs: [] };

async function load() {
  const s = { ...DEFAULTS, ...(await chrome.storage.local.get([...Object.keys(DEFAULTS), "stats"])) };
  $("auto").checked = !!s.autoReload;
  $("interval").value = String(s.intervalMin);
  const st = s.stats || {};
  const ago = (t) => `${Math.round((Date.now() - t) / 1000)}s ago`;
  $("hello").textContent = st.lastHello ? `yes, ${ago(st.lastHello)}` : "no — reload the BetMGM tab";
  $("captured").textContent = st.captured ? `${st.captured} (last ${ago(st.lastCaptured)})` : "0";
  $("last").textContent = st.lastSent ? `${ago(st.lastSent)} (${st.sent} ok, ${st.failed || 0} failed)` : `none (${st.failed || 0} failed)`;
  $("err").textContent = st.lastError || "";

  const list = $("tabs"); list.replaceChildren();
  for (const id of s.markedTabs) {
    try {
      const t = await chrome.tabs.get(id);
      const li = document.createElement("li"); li.textContent = (t.title || t.url).slice(0, 60); list.append(li);
    } catch {}
  }
  if (!list.children.length) { const li = document.createElement("li"); li.textContent = "none"; list.append(li); }

  try {
    const r = await fetch(`http://127.0.0.1:${s.port}/api/health`);
    const h = await r.json();
    const mgm = h.sources.find((x) => x.name === "betmgm");
    $("server").textContent = mgm ? `running · BetMGM ${mgm.health} · ${mgm.quotes} prices` : "running (BetMGM not enabled)";
    $("server").className = "ok";
  } catch {
    $("server").textContent = "not running"; $("server").className = "bad";
  }
}

async function currentTab() {
  const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
  return t;
}

$("auto").addEventListener("change", (e) => chrome.storage.local.set({ autoReload: e.target.checked }));
$("interval").addEventListener("change", (e) => chrome.storage.local.set({ intervalMin: Number(e.target.value) }));
$("mark").addEventListener("click", async () => {
  const t = await currentTab();
  if (!t?.url?.startsWith("https://www.pa.betmgm.com/")) { $("err").textContent = "Open a BetMGM page first."; return; }
  const { markedTabs = [] } = await chrome.storage.local.get("markedTabs");
  if (!markedTabs.includes(t.id)) await chrome.storage.local.set({ markedTabs: [...markedTabs, t.id] });
  load();
});
$("unmark").addEventListener("click", async () => {
  const t = await currentTab();
  const { markedTabs = [] } = await chrome.storage.local.get("markedTabs");
  await chrome.storage.local.set({ markedTabs: markedTabs.filter((id) => id !== t?.id) });
  load();
});

load();
