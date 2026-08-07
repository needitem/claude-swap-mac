"""The menu bar item and the dashboard window.

The window is a WKWebView rendering :mod:`render`'s HTML — laying out a dozen
progress bars is a thing HTML is good at and AppKit's box model is not — with a
single script-message channel back for the "전환" buttons.
"""

from __future__ import annotations

import json
import threading

import AppKit
import objc
import rumps
import WebKit
from Foundation import NSMakeRect, NSObject, NSOperationQueue

from cswap_dashboard import cswap, render

ICON = "⇄"
REFRESH_SECONDS = 60
WINDOW_WIDTH = 560


def _on_main(fn, *args) -> None:
    """Run ``fn`` on the main thread; AppKit tolerates nothing else."""
    NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: fn(*args))


def _local_part(email: str) -> str:
    return email.split("@", 1)[0] if "@" in email else email


def _pct(window: object) -> float | None:
    if isinstance(window, dict) and isinstance(window.get("pct"), (int, float)):
        return float(window["pct"])
    return None


class _Bridge(NSObject):
    """WKScriptMessageHandler — receives ``{action, number}`` from the page."""

    def initWithHandler_(self, handler):
        self = objc.super(_Bridge, self).init()
        if self is None:
            return None
        self._handler = handler
        return self

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        body = message.body()
        try:
            action = body["action"]
            number = int(body["number"])
        except (KeyError, TypeError, ValueError):
            return
        self._handler(action, number)


class _NavDelegate(NSObject):
    """Tells us when a load has finished, so we can measure the laid-out page."""

    def initWithHandler_(self, handler):
        self = objc.super(_NavDelegate, self).init()
        if self is None:
            return None
        self._handler = handler
        return self

    def webView_didFinishNavigation_(self, webview, navigation):
        self._handler()


class Dashboard:
    """The window. Created lazily so the app costs nothing until first opened."""

    def __init__(self, on_switch):
        self._on_switch = on_switch
        self._window = None
        self._webview = None
        self._loaded = False
        self._bridge = None
        self._nav = None
        self._fit_pending = False

    def _build(self, initial_height):
        config = WebKit.WKWebViewConfiguration.alloc().init()
        self._bridge = _Bridge.alloc().initWithHandler_(self._on_switch)
        config.userContentController().addScriptMessageHandler_name_(self._bridge, "cswap")

        rect = NSMakeRect(0, 0, WINDOW_WIDTH, initial_height)
        self._webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(rect, config)
        # Otherwise the page paints white behind our dark theme for a frame.
        self._webview.setValue_forKey_(False, "drawsBackground")
        self._nav = _NavDelegate.alloc().initWithHandler_(self._on_load_finished)
        self._webview.setNavigationDelegate_(self._nav)

        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
            | AppKit.NSWindowStyleMaskResizable
        )
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        window.setTitle_("Claude Swap")
        window.setMinSize_(AppKit.NSMakeSize(420, 150))  # never fight the fitted height
        # An accessory app has no Dock icon to restore from, so closing the
        # window must only hide it — releasing it would strand the app with no
        # way back except the menu bar (which is fine) but also leak the
        # webview's message handler.
        window.setReleasedWhenClosed_(False)
        window.setContentView_(self._webview)
        window.center()
        self._window = window

    def show(self, payload, error):
        first = self._window is None
        if first:
            # Start at the estimated height so the measured fit below is a
            # nudge rather than a visible jump.
            self._build(render.content_height(payload, error))
            # Only on first open: once the user has sized the window, resizing
            # it out from under them on a later open would be rude.
            self._fit_pending = True
        self.update(payload, error)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._window.makeKeyAndOrderFront_(None)
        frame, screen = self._window.frame(), AppKit.NSScreen.mainScreen().frame()
        print(
            f"window at ({frame.origin.x:.0f},{frame.origin.y:.0f}) "
            f"{frame.size.width:.0f}x{frame.size.height:.0f} "
            f"visible={bool(self._window.isVisible())} "
            f"mainScreen={screen.size.width:.0f}x{screen.size.height:.0f} "
            f"windowNumber={self._window.windowNumber()}",
            flush=True,
        )

    def _on_load_finished(self):
        """Fit the window to the page once it has actually been laid out.

        Estimating the height from the payload was close but not reliable —
        Korean text wraps differently than the CSS box maths assumed, and the
        window ended up one scrollbar short. Asking the rendered document is
        exact and cannot drift when the stylesheet changes.
        """
        if not self._fit_pending:
            return
        self._fit_pending = False
        self._webview.evaluateJavaScript_completionHandler_(
            "document.documentElement.scrollHeight", self._apply_height
        )

    def _apply_height(self, result, error):
        if error is not None or result is None:
            return
        window = self._window
        # setFrame takes the *frame* height, which includes the title bar.
        chrome = window.frame().size.height - window.contentRectForFrameRect_(
            window.frame()
        ).size.height
        height = max(150.0, min(900.0, float(result) + chrome))
        frame = window.frame()
        window.setFrame_display_(
            AppKit.NSMakeRect(frame.origin.x, frame.origin.y, frame.size.width, height),
            True,
        )
        window.center()
        fitted = window.frame()
        print(
            f"fitted to content {float(result):.0f}pt -> window "
            f"{fitted.size.width:.0f}x{fitted.size.height:.0f} "
            f"at ({fitted.origin.x:.0f},{fitted.origin.y:.0f}) "
            f"windowNumber={window.windowNumber()}",
            flush=True,
        )

    @property
    def visible(self) -> bool:
        return self._window is not None and bool(self._window.isVisible())

    def update(self, payload, error):
        """Repaint. Cheap enough to call on every poll."""
        if self._webview is None:
            return
        if not self._loaded:
            self._webview.loadHTMLString_baseURL_(render.render(payload, error), None)
            self._loaded = True
            return
        body = json.dumps(render.render_body(payload, error))
        self._webview.evaluateJavaScript_completionHandler_(
            f"document.body.innerHTML = {body};", None
        )


class DashboardApp(rumps.App):
    def __init__(self):
        super().__init__(ICON, quit_button=None)
        self.payload: dict | None = None
        self.error: str | None = None
        self.dashboard = Dashboard(self._handle_switch)
        self._busy = threading.Lock()
        # Launching the app from Finder should show the thing the app is for.
        # Deferred until the first payload lands so the window never appears
        # empty and then jumps.
        self._opened_once = False
        self.menu = ["대시보드 열기"]
        self.refresh(None)

    # ---- data ------------------------------------------------------------
    def _fetch(self, then=None):
        """Poll cswap off the main thread; the CLI can block on the network."""
        if not self._busy.acquire(blocking=False):
            return  # a poll is already in flight; its result is as fresh
        def work():
            try:
                payload, error = cswap.list_accounts(), None
            except cswap.CswapError as exc:
                payload, error = None, str(exc)
            except Exception as exc:  # never let a poll kill the app
                payload, error = None, f"{type(exc).__name__}: {exc}"
            finally:
                self._busy.release()
            _on_main(self._apply, payload, error, then)

        threading.Thread(target=work, daemon=True).start()

    def _apply(self, payload, error, then=None):
        # A failed poll keeps the last good payload on screen — a transient
        # network blip should not blank a dashboard that was correct a minute
        # ago. The error only takes over when there is nothing to fall back to.
        if payload is not None:
            self.payload, self.error = payload, None
        else:
            self.error = error
        self.title = self._status_title()
        self._rebuild_menu()
        shown = None if self.payload else self.error
        if not self._opened_once:
            self._opened_once = True
            self.dashboard.show(self.payload, shown)
        elif self.dashboard.visible:
            self.dashboard.update(self.payload, shown)
        if then:
            then()

    # ---- presentation ----------------------------------------------------
    def _status_title(self) -> str:
        accounts = (self.payload or {}).get("accounts") or []
        active = next((a for a in accounts if isinstance(a, dict) and a.get("active")), None)
        if not active:
            return ICON
        usage = active.get("usage") or active.get("lastGoodUsage") or {}
        bits = [_local_part(str(active.get("alias") or active.get("email") or ""))]
        for key in ("fiveHour", "sevenDay"):
            pct = _pct(usage.get(key))
            if pct is not None:
                bits.append(f"{pct:.0f}%")
        return f"{ICON} " + " · ".join(b for b in bits if b)

    def _rebuild_menu(self):
        self.menu.clear()
        items = [rumps.MenuItem("대시보드 열기", callback=self.open_dashboard), None]
        for acc in (self.payload or {}).get("accounts") or []:
            if not isinstance(acc, dict) or not isinstance(acc.get("number"), int):
                continue
            usage = acc.get("usage") or acc.get("lastGoodUsage") or {}
            five, seven = _pct(usage.get("fiveHour")), _pct(usage.get("sevenDay"))
            summary = " · ".join(f"{p:.0f}%" for p in (five, seven) if p is not None) or "—"
            mark = "● " if acc.get("active") else "○ "
            label = f"{mark}{acc['number']}  {_local_part(str(acc.get('email') or ''))}  {summary}"
            item = rumps.MenuItem(label, callback=self._make_switch(acc["number"]))
            if acc.get("active"):
                item.set_callback(None)  # already there; nothing to do
            items.append(item)
        items += [None, rumps.MenuItem("새로고침", callback=self.refresh),
                  rumps.MenuItem("종료", callback=rumps.quit_application)]
        for item in items:
            self.menu.add(item) if item is not None else self.menu.add(rumps.separator)

    # ---- actions ---------------------------------------------------------
    def open_dashboard(self, _=None):
        self.dashboard.show(self.payload, None if self.payload else self.error)
        self._fetch()

    def refresh(self, _=None):
        self._fetch()

    def _make_switch(self, number: int):
        def callback(_):
            self._handle_switch("switch", number)
        return callback

    def _handle_switch(self, action: str, number: int):
        if action != "switch":
            return

        def work():
            try:
                cswap.switch_to(number)
                err = None
            except cswap.CswapError as exc:
                err = str(exc)
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
            _on_main(self._after_switch, err)

        threading.Thread(target=work, daemon=True).start()

    def _after_switch(self, err):
        if err:
            rumps.notification("Claude Swap", "전환 실패", err)
        self._fetch()

    @rumps.timer(REFRESH_SECONDS)
    def _tick(self, _):
        self._fetch()


def main() -> int:
    # rumps never sets an activation policy, so a framework Python would park a
    # "Python" icon in the Dock. Accessory keeps the status item and lets the
    # dashboard window show, without a Dock icon or a Cmd-Tab entry.
    AppKit.NSApplication.sharedApplication().setActivationPolicy_(
        AppKit.NSApplicationActivationPolicyAccessory
    )
    DashboardApp().run()
    return 0
