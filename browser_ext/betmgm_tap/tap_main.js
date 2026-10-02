// Runs inside BetMGM's page (MAIN world). Passive: it only observes responses the page itself requested.
// For the two odds endpoints it copies the JSON and hands it to tap_relay.js via window.postMessage.
// It never issues requests, never changes requests or responses.
//
// Two independent hooks, so it works however the page's framework (Angular + zone.js) calls fetch:
//   1. wrap window.fetch / XMLHttpRequest
//   2. hook Response.prototype.json/text: whenever the page reads an odds response body, copy it
// A WeakSet prevents sending the same response twice.
(() => {
  const WANTED = ["/cds-api/bettingoffer/fixtures", "/cds-api/bettingoffer/fixture-view"];
  const done = new WeakSet();
  let seen = 0;

  function wantedPath(url) {
    try {
      const path = new URL(url, location.href).pathname;  // query (incl. access id) is dropped
      return WANTED.includes(path) ? path : null;
    } catch {
      return null;
    }
  }

  function forward(path, data, via) {
    if (!data || typeof data !== "object") return;
    seen += 1;
    console.debug(`[EVC tap] captured ${path} via ${via} (#${seen})`);
    window.postMessage({ __evcTap: "betmgm", path, data }, location.origin);
  }

  // Hook 2: Response body readers (catches fetch even if the framework kept its own fetch reference).
  const origJson = Response.prototype.json;
  const origText = Response.prototype.text;
  Response.prototype.json = function () {
    const p = origJson.apply(this, arguments);
    const path = wantedPath(this.url);
    if (path && this.ok && !done.has(this)) {
      done.add(this);
      p.then((d) => forward(path, d, "response.json")).catch(() => {});
    }
    return p;
  };
  Response.prototype.text = function () {
    const p = origText.apply(this, arguments);
    const path = wantedPath(this.url);
    if (path && this.ok && !done.has(this)) {
      done.add(this);
      p.then((t) => { try { forward(path, JSON.parse(t), "response.text"); } catch {} }).catch(() => {});
    }
    return p;
  };

  // Hook 1a: fetch
  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : input && input.url;
    const path = wantedPath(url);
    const p = origFetch.apply(this, arguments);
    if (path) {
      p.then((resp) => {
        if (!resp.ok || done.has(resp)) return;
        done.add(resp);
        origJson.call(resp.clone()).then((d) => forward(path, d, "fetch")).catch(() => {});
      }).catch(() => {});
    }
    return p;  // the page gets the original, untouched response
  };

  // Hook 1b: XMLHttpRequest
  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__evcPath = wantedPath(url);
    if (this.__evcPath) {
      this.addEventListener("load", () => {
        if (this.status < 200 || this.status >= 300) return;
        try {
          const d = this.responseType === "json" ? this.response
                  : (this.responseType === "" || this.responseType === "text") ? JSON.parse(this.responseText)
                  : null;
          forward(this.__evcPath, d, "xhr");
        } catch {}
      });
    }
    return origOpen.apply(this, arguments);
  };

  console.debug("[EVC tap] active on", location.pathname);
  window.postMessage({ __evcTap: "betmgm-hello" }, location.origin);
})();
