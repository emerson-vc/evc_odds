// Isolated content script: receives copies from tap_main.js (same page only) and passes them to the
// extension's background worker, which is allowed to talk to the local scanner.
window.addEventListener("message", (event) => {
  if (event.source !== window || event.origin !== location.origin) return;
  const msg = event.data;
  if (msg && msg.__evcTap === "betmgm-hello") {
    chrome.runtime.sendMessage({ type: "hello", url: location.pathname }).catch(() => {});
    return;
  }
  if (!msg || msg.__evcTap !== "betmgm" || typeof msg.path !== "string") return;
  chrome.runtime.sendMessage({ type: "payload", path: msg.path, data: msg.data }).catch((e) =>
    console.warn("[EVC tap] couldn't reach extension background:", e && e.message));
});
