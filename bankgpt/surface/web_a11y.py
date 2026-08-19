from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Frame, Locator as PwLocator, Page

from bankgpt.artifact.schema import Locator, Target


@dataclass
class SurfaceSnapshot:
    url: str
    title: str
    aria: str
    busy: bool = False


class SurfaceAdapter:
    def snapshot(self) -> SurfaceSnapshot: ...
    def find(self, target: Target, timeout_ms: int) -> PwLocator: ...
    def act(self, action: str, target: Target | None, value: str | None) -> None: ...
    def extract(self, target: Target, timeout_ms: int) -> str: ...
    def text_present(self, text: str) -> bool: ...
    def current_url(self) -> str: ...


class WebA11yAdapter(SurfaceAdapter):
    """Playwright adapter: accessibility-oriented locators, not CSS."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def current_url(self) -> str:
        return self.page.url

    def snapshot(self) -> SurfaceSnapshot:
        parts = [self._aria(self.page)]
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            try:
                parts.append(f"[iframe {frame.name or frame.url}]\n{self._aria(frame)}")
            except Exception:
                continue
        aria = "\n".join(p for p in parts if p)
        busy = bool(self.page.locator("[aria-busy=true]").count())
        return SurfaceSnapshot(url=self.page.url, title=self.page.title(), aria=aria, busy=busy)

    def _aria(self, ctx: Page | Frame) -> str:
        try:
            return ctx.locator("body").aria_snapshot(timeout=3000)
        except Exception:
            return ctx.inner_text("body")[:4000]

    def find(self, target: Target, timeout_ms: int) -> PwLocator:
        last_err: Exception | None = None
        per = max(250, min(timeout_ms, timeout_ms // max(1, len(target.locators) * 2)))
        for loc in target.locators:
            for ctx in self._contexts():
                try:
                    handle = self._resolve_in(ctx, loc)
                    handle.first.wait_for(state="visible", timeout=per)
                    return handle.first
                except Exception as exc:
                    last_err = exc
        raise LookupError(f"locator miss: {target.model_dump()} ({last_err})")

    def _contexts(self) -> list[Page | Frame]:
        frames: list[Page | Frame] = [self.page]
        for frame in self.page.frames:
            if frame != self.page.main_frame:
                frames.append(frame)
        return frames

    def _resolve_in(self, ctx: Page | Frame, loc: Locator) -> PwLocator:
        if loc.by == "role":
            kwargs: dict = {}
            if loc.name:
                kwargs["name"] = loc.name
                kwargs["exact"] = loc.exact
            return ctx.get_by_role(loc.role or "generic", **kwargs)
        if loc.by == "label":
            return ctx.get_by_label(loc.text or loc.name or "", exact=loc.exact)
        if loc.by == "placeholder":
            return ctx.get_by_placeholder(loc.text or loc.name or "", exact=loc.exact)
        if loc.by == "text":
            return ctx.get_by_text(loc.text or loc.name or "", exact=loc.exact)
        if loc.by == "table_cell":
            # Header cell then value in the same row.
            row = ctx.locator("tr", has=ctx.get_by_text(loc.column or loc.name or "", exact=False))
            cells = row.locator("td")
            return cells.nth(loc.index if loc.index is not None else 0)
        if loc.by == "nth":
            return ctx.get_by_role(loc.role or "generic").nth(loc.index or 0)
        raise ValueError(f"unknown locator kind {loc.by}")

    def act(
        self,
        action: str,
        target: Target | None,
        value: str | None,
        timeout_ms: int = 8000,
    ) -> None:
        if action == "wait":
            self.page.wait_for_timeout(int(value or 500))
            return
        if action == "navigate":
            if not value:
                raise ValueError("navigate requires a URL")
            self.page.goto(value)
            return
        if target is None:
            raise ValueError(f"{action} requires a target")
        handle = self.find(target, timeout_ms=timeout_ms)
        if action == "click":
            handle.click()
        elif action == "fill":
            handle.fill(value or "")
        elif action == "select":
            handle.select_option(value or "")
        elif action == "press":
            handle.press(value or "Enter")
        elif action == "dismiss":
            handle.click()
        elif action == "extract":
            return
        else:
            raise ValueError(f"unsupported action {action}")

    def extract(self, target: Target, timeout_ms: int) -> str:
        handle = self.find(target, timeout_ms)
        return (handle.inner_text() or "").strip()

    def text_present(self, text: str) -> bool:
        try:
            self.page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=500)
            return True
        except Exception:
            for frame in self.page.frames:
                try:
                    if frame.get_by_text(text, exact=False).count():
                        return True
                except Exception:
                    continue
            return text in (self.snapshot().aria or "")

    def detect(self, locators: list[Locator] | None, text: str | None, busy: bool | None) -> bool:
        snap = self.snapshot()
        if busy is True and snap.busy:
            return True
        needles: list[str] = []
        if text:
            needles.append(text)
        if locators:
            for loc in locators:
                if loc.name:
                    needles.append(loc.name)
                if loc.text:
                    needles.append(loc.text)
        aria_l = snap.aria.lower()
        for needle in needles:
            if needle.lower() in aria_l:
                return True
        if text and self.text_present(text):
            return True
        return False
