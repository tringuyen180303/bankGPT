from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from bankgpt.surface.web_a11y import WebA11yAdapter

Owner = Literal["automation", "human"]

# Survives navigation so operator clicks in the headed window are logged.
_HUMAN_INIT = """
(() => {
  window.__bankgptHuman = window.__bankgptHuman || [];
  if (window.__bankgptBound) return;
  window.__bankgptBound = true;
  const push = (row) => window.__bankgptHuman.push(row);
  document.addEventListener('click', (e) => {
    const el = e.target;
    if (!el) return;
    if (el.closest && el.closest('#bankgpt-op')) return;
    const name = (el.getAttribute && (el.getAttribute('aria-label') || el.innerText || el.value)) || '';
    push({ ts: Date.now(), actor: 'human', action: 'click', name: String(name).trim().slice(0, 120), tag: el.tagName });
  }, true);
  document.addEventListener('change', (e) => {
    const el = e.target;
    if (!el) return;
    const sensitive = (el.type || '').toLowerCase() === 'password';
    push({
      ts: Date.now(),
      actor: 'human',
      action: 'fill',
      name: (el.getAttribute('aria-label') || el.name || '').slice(0, 80),
      value: sensitive ? '[REDACTED:SECRET]' : String(el.value || '').slice(0, 80),
    });
  }, true);
})();
"""

_OVERLAY_JS = """
(reason) => {
  const old = document.getElementById('bankgpt-op');
  if (old) old.remove();
  const bar = document.createElement('div');
  bar.id = 'bankgpt-op';
  bar.setAttribute('role', 'dialog');
  bar.setAttribute('aria-label', 'Operator control');
  bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#7a1f1f;color:#fff;padding:12px 16px;font:14px/1.4 system-ui,sans-serif;display:flex;gap:10px;align-items:center;flex-wrap:wrap;box-shadow:0 4px 16px #0008';
  const msg = document.createElement('div');
  msg.style.flex = '1';
  msg.innerHTML = '<strong>You are in control of this session.</strong> '
    + 'Stay in this Chromium window. Close account is the last link under the member table. '
    + (reason ? ('Reason: ' + reason) : '');
  const resume = document.createElement('button');
  resume.textContent = 'Resume automation';
  resume.style.cssText = 'padding:8px 12px;font-weight:600;cursor:pointer';
  resume.onclick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    window.__bankgptCmd = 'resume';
    resume.textContent = 'Resuming…';
  };
  const abort = document.createElement('button');
  abort.textContent = 'Abort';
  abort.style.cssText = 'padding:8px 12px;cursor:pointer';
  abort.onclick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    window.__bankgptCmd = 'abort';
    abort.textContent = 'Aborting…';
  };
  bar.append(msg, resume, abort);
  (document.body || document.documentElement).prepend(bar);
}
"""


@dataclass
class Session:
    run_id: str
    evidence_dir: Path
    headed: bool
    owner: Owner = "automation"
    _pw: Playwright | None = None
    browser: Browser | None = None
    context: BrowserContext | None = None
    page: Page | None = None
    adapter: WebA11yAdapter | None = None
    _started: bool = field(default=False, repr=False)
    _handoff_reason: str = field(default="", repr=False)

    def start(self) -> WebA11yAdapter:
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=not self.headed)
        self.context = self.browser.new_context(viewport={"width": 1024, "height": 768})
        self.context.add_init_script(_HUMAN_INIT)
        self.context.tracing.start(screenshots=True, snapshots=True)
        self.page = self.context.new_page()
        self.page.on("load", lambda _page: self._repaint_overlay())
        self.adapter = WebA11yAdapter(self.page)
        self._started = True
        return self.adapter

    def poll_operator_command(self) -> str | None:
        """Read Resume/Abort from the overlay. Uses Playwright I/O so the page stays live."""
        if self.page:
            try:
                cmd = self.page.evaluate("() => window.__bankgptCmd || null")
                if cmd in {"resume", "abort"}:
                    dest = self.evidence_dir / "control.json"
                    dest.write_text(json.dumps({"command": cmd}) + "\n")
                    return cmd
            except Exception:
                pass
            try:
                self.page.wait_for_timeout(300)
            except Exception:
                pass
        dest = self.evidence_dir / "control.json"
        if dest.exists():
            try:
                cmd = json.loads(dest.read_text()).get("command")
                if cmd in {"resume", "abort"}:
                    return cmd
            except Exception:
                return None
        return None

    def enter_operator_mode(self, reason: str) -> None:
        self.owner = "human"
        self._handoff_reason = reason
        if not self.page:
            return
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        self._paint_overlay()

    def exit_operator_mode(self) -> None:
        self.owner = "automation"
        self._handoff_reason = ""
        if not self.page:
            return
        try:
            self.page.evaluate("() => { const n = document.getElementById('bankgpt-op'); if (n) n.remove(); }")
        except Exception:
            pass

    def _repaint_overlay(self) -> None:
        if self.owner == "human":
            self._paint_overlay()

    def _paint_overlay(self) -> None:
        if not self.page:
            return
        try:
            self.page.evaluate(_OVERLAY_JS, self._handoff_reason)
        except Exception:
            pass

    def screenshot(self, name: str = "failure.png") -> Path | None:
        if not self.page:
            return None
        path = self.evidence_dir / name
        self.page.screenshot(path=str(path), full_page=True)
        return path

    def set_owner(self, owner: Owner) -> None:
        self.owner = owner

    def collect_human_actions(self) -> list:
        if not self.page:
            return []
        try:
            return self.page.evaluate("() => window.__bankgptHuman || []") or []
        except Exception:
            return []

    def close(self) -> None:
        if self.context:
            try:
                trace = self.evidence_dir / "trace.zip"
                self.context.tracing.stop(path=str(trace))
            except Exception:
                pass
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._started = False
