# EVC BetMGM Tap (personal Chrome extension)

BetMGM blocks programs (Cloudflare bot protection), so the scanner can't fetch BetMGM odds itself. This
extension copies the odds data **BetMGM pages you have open in your own Chrome already receive** and sends it
to your local scanner. It sends no requests to BetMGM and never changes what the page sees.

BetMGM pages don't refresh pregame odds on their own; odds arrive when a page loads. So either reload
BetMGM pages yourself, or turn on the extension's **auto-reload** (off by default) for tabs you mark.

## Install (once)
1. Chrome → `chrome://extensions` → turn on **Developer mode** (top right).
2. **Load unpacked** → choose this folder: `EVC_Odds/browser_ext/betmgm_tap`.
3. Pin it (puzzle-piece icon → pin "EVC BetMGM Tap").
4. **Reload every BetMGM tab that was already open.** Chrome only adds the extension to pages loaded after it
   was installed (or after you click the extension's reload icon).

## If it isn't working
Open the popup. Each line tells you where it stops:
- *Tap active in a BetMGM tab: no* → reload the BetMGM tab.
- *Odds responses captured: 0* → the page hasn't loaded odds since the tab was reloaded; open the NFL page or a game.
  In the BetMGM tab's DevTools console, `[EVC tap]` lines show what was seen.
- *Sent to scanner: none, N failed* → the red line says why (scanner not running, or the scanner's reason for
  rejecting it, which it also prints in its terminal).

## Use
1. Start the scanner: `.venv/bin/python -m src.api.app` (in `EVC_Odds`).
2. In Chrome, open the BetMGM **NFL page** (all games' lines in one load). Open any **game pages** whose
   player props you want.
3. The extension icon popup should show *Scanner: running · BetMGM LIVE · N prices*. The odds screen
   (http://127.0.0.1:8000) gets a filled-in BetMGM column.
4. Optional: in each BetMGM tab you want kept fresh, open the popup → **Mark this tab**, then tick
   **Auto-reload marked tabs** and pick an interval. Tabs reload one at a time, 20 s apart, and the tab
   you're looking at is never reloaded.

## Notes
- Only `/cds-api/bettingoffer/fixtures` and `/fixture-view` responses are copied; the URL query (which holds
  BetMGM's site access id) is dropped before anything leaves the page.
- Data goes only to `http://127.0.0.1` (your machine). If the scanner runs on another port, change `port` in
  the extension's storage (default 8000).
- After editing these files, click the reload icon on the extension card in `chrome://extensions`.
