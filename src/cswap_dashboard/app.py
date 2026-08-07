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

from cswap_dashboard import add_account, cswap, render

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
        items += [None,
                  rumps.MenuItem("계정 추가…", callback=self.add_account),
                  rumps.MenuItem("새로고침", callback=self.refresh),
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

    # ---- adding an account ------------------------------------------------
    def add_account(self, _=None):
        """Whole add flow, in-app. See :mod:`add_account` for why the browser
        step cannot be removed."""
        choice = rumps.alert(
            title="계정 추가",
            message=(
                "브라우저에서 Anthropic 로그인 페이지가 열립니다. 로그인이 끝나면 "
                "등록까지 자동으로 진행됩니다.\n\n"
                "다른 계정을 추가하려면 브라우저에서 claude.com에 먼저 로그아웃하세요. "
                "이미 로그인된 계정이 있으면 그 계정으로 그대로 승인됩니다. "
                "(다음 창에 뜨는 주소를 시크릿 창에 붙여넣어도 됩니다)\n\n"
                "지금 계정은 먼저 백업되며, 취소해도 로그인 상태는 그대로입니다."
            ),
            ok="브라우저로 로그인",
            cancel="취소",
            other="토큰 붙여넣기",
        )
        if choice == 1:
            self._add_via_browser()
        elif choice == -1:  # NSAlertOtherReturn
            self._add_via_token()

    def _add_via_token(self):
        response = rumps.Window(
            title="토큰으로 계정 추가",
            message=(
                "setup-token(sk-ant-oat…) 또는 Console API 키(sk-ant-api…)를 붙여넣으세요.\n"
                "터미널에서 `claude setup-token`으로 만들 수 있습니다."
            ),
            ok="추가",
            cancel="취소",
            dimensions=(340, 24),
        ).run()
        token = (response.text or "").strip()
        if not response.clicked or not token:
            return
        if not add_account.looks_like_token(token):
            rumps.alert("추가할 수 없음", "sk-ant-oat… 또는 sk-ant-api… 로 시작해야 합니다.")
            return
        self._background(lambda: cswap.add_token(token), "토큰으로 계정을 추가했습니다")

    def _add_via_browser(self):
        def stage_one():
            try:
                # Store the login we are about to replace. Note we deliberately
                # do NOT log out first: logout revokes the refresh token
                # server-side, which would make this very backup useless.
                add_account.protect_current_login()
                previous = add_account.active_account_number()
                session = add_account.start_login()
                url = session.wait_for_url()
            except Exception as exc:
                _on_main(self._add_failed, f"{exc}", None)
                return
            _on_main(self._ask_for_code, session, url, previous)

        threading.Thread(target=stage_one, daemon=True).start()

    def _ask_for_code(self, session, url, previous):
        response = rumps.Window(
            title="브라우저에서 로그인",
            message=(
                "브라우저에서 로그인을 마치세요.\n\n"
                "• 코드가 표시되면 아래에 붙여넣고 [완료]\n"
                "• 코드 없이 자동으로 끝났으면 그냥 [완료]\n\n"
                "브라우저가 열리지 않았거나 다른 계정으로 로그인하려면 "
                "이 주소를 (시크릿 창에) 여세요:\n" + url
            ),
            ok="완료",
            cancel="취소",
            dimensions=(340, 24),
        ).run()
        if not response.clicked:
            # Nothing was replaced — no logout happened, so the current login is
            # still the one it always was. Just stop the login process.
            self._background(lambda: session.cancel(), "계정 추가를 취소했습니다")
            return

        code = (response.text or "").strip()

        def stage_two():
            try:
                session.finish(code)
                cswap.add_current()
            except Exception as exc:
                # A failed sign-in leaves the old credentials in place, but a
                # sign-in that *succeeded* as another account before `cswap add`
                # failed has replaced them — restore in that case.
                restored = self._restore_if_replaced(previous)
                _on_main(self._add_failed, f"{exc}", previous if restored else None)
                return
            _on_main(self._add_done, "계정을 추가했습니다")

        threading.Thread(target=stage_two, daemon=True).start()

    @staticmethod
    def _restore_if_replaced(previous) -> bool:
        """Put ``previous`` back only if the active login is no longer it."""
        if previous is None:
            return False
        try:
            if add_account.active_account_number() == previous:
                return False
        except Exception:
            pass
        DashboardApp._restore(previous)
        return True

    @staticmethod
    def _restore(previous):
        if previous is None:
            return
        try:
            cswap.restore(previous)
        except cswap.CswapError:
            pass  # nothing better to try; the error dialog already explains

    def _background(self, work, success_message):
        def run():
            try:
                work()
            except Exception as exc:
                _on_main(self._add_failed, f"{exc}", None)
                return
            _on_main(self._add_done, success_message)

        threading.Thread(target=run, daemon=True).start()

    def _add_done(self, message):
        rumps.notification("Claude Swap", "", message)
        self._fetch()

    def _add_failed(self, message, previous):
        tail = "\n\n이전 계정은 복구했습니다." if previous is not None else ""
        rumps.alert("계정 추가 실패", message + tail)
        self._fetch()

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
