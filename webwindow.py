"""Marquee's window on Windows: the same ui.html in pywebview, which draws it with the Edge
WebView2 that Windows 10 and 11 already have. Linux keeps the GTK window in marquee.py.

On Linux the page talks to Python through WebKit's window.webkit.messageHandlers.marquee.
Here SHIM, put in front of the page's own script, supplies window.marqueePost, which the page
uses when it exists, and hands each message to Bridge.post; replies go back through
window.fromApp exactly as on Linux, so ui.html is the same file on both. SHIM also does the window furniture: GTK hands a drag or an edge resize to the
window manager, pywebview cannot, so the page follows the mouse itself and moves the window
(the way pywebview's own drag works, in its js/customize.js).

No frame, as on Linux. Windows 11 rounds the corners of the window itself; Windows 10 leaves
them square. The page's slab is drawn square here so it fills the window either way.
"""
import json
import sys

import webview

SHIM = r"""<script>
(() => {
  const MIN_W = %(min_w)d, MIN_H = %(min_h)d;
  const queue = [];
  let ready = false, down = null;
  const send = m => window.pywebview.api.post(m);
  window.addEventListener("pywebviewready", () => { ready = true; queue.splice(0).forEach(send); });
  // The page asks for "drag" or "resize" from its own mousedown; this remembers where that was.
  document.addEventListener("mousedown", e => { down = e; }, true);

  let busy = false, next = null;
  function frame(box) {             // one move in flight at a time; the newest position wins
    next = box;
    if (busy || !ready) return;
    busy = true; const b = next; next = null;
    window.pywebview.api.frame(...b).finally(() => { busy = false; if (next) frame(next); });
  }
  function follow(kind, edge) {
    if (!down) return;
    const sx = down.screenX, sy = down.screenY;
    const x0 = window.screenX, y0 = window.screenY, w0 = window.outerWidth, h0 = window.outerHeight;
    const move = e => {
      const dx = e.screenX - sx, dy = e.screenY - sy;
      if (kind === "drag") return frame([x0 + dx, y0 + dy, w0, h0]);
      let x = x0, y = y0, w = w0, h = h0;
      if (edge.includes("e")) w = Math.max(MIN_W, w0 + dx);
      if (edge.includes("s")) h = Math.max(MIN_H, h0 + dy);
      if (edge.includes("w")) { w = Math.max(MIN_W, w0 - dx); x = x0 + w0 - w; }
      if (edge.includes("n")) { h = Math.max(MIN_H, h0 - dy); y = y0 + h0 - h; }
      frame([x, y, w, h]);
    };
    const up = () => { removeEventListener("mousemove", move); removeEventListener("mouseup", up); };
    addEventListener("mousemove", move); addEventListener("mouseup", up);
  }
  window.marqueePost = m => {
    const o = JSON.parse(m);
    if (o.method === "drag" || o.method === "resize") {
      follow(o.method, o.args[0] || "");
      window.fromApp && window.fromApp("reply", {id: o.id, result: null});
      return;
    }
    ready ? send(m) : queue.push(m);
  };
})();
</script>
"""


class Bridge:
    """What SHIM can call. pywebview runs each call on its own thread and hides names that
    start with an underscore from the page."""

    def __init__(self, app):
        self._app = app

    def post(self, message):
        self._app.on_message(message)

    def frame(self, x, y, w, h):
        win = self._app.window
        w, h = int(w), int(h)
        if (w, h) != (win.width, win.height):
            win.resize(w, h)
        win.move(int(x), int(y))


class App:
    def __init__(self, make_api, theme, page, geometry, remember, size, selftest=False):
        self.exit_code = 1 if selftest else 0
        self._remember = remember
        self._maximized = False
        zoom = float(theme.get("zoom", 1.5))
        # theme.json is set for a 4K panel at scale 1; Windows scales for the screen itself, so
        # the page runs at zoom 1 and the window is sized in Windows' own (logical) pixels.
        self._theme = {**theme, "zoom": 1, "corner_radius": 0}
        w, h = int(size[0] / zoom), int(size[1] / zoom)
        x = y = None
        if webview.screens:
            # Fit the screen with room for the taskbar, and start in the middle of it: left to
            # Windows, a 1024x768 screen got the window low and running under the taskbar.
            screen = webview.screens[0]
            w, h = min(w, int(screen.width * 0.9)), min(h, int(screen.height * 0.85))
            x, y = (screen.width - w) // 2, max(0, (screen.height - h) // 3)
        self.min_size = (min(int(640 / zoom), w), min(int(700 / zoom), h))
        with open(page, encoding="utf-8") as f:
            html = f.read()
        shim = SHIM % {"min_w": self.min_size[0], "min_h": self.min_size[1]}
        html = html.replace("<script>", shim + "<script>", 1)

        self.api = make_api(self)
        self.window = webview.create_window(
            "Marquee", html=html, js_api=Bridge(self), frameless=True, easy_drag=False,
            width=geometry.get("w", w), height=geometry.get("h", h),
            x=geometry.get("x", x), y=geometry.get("y", y), min_size=self.min_size,
            background_color=theme.get("background") or "#1b2028", text_select=True)
        # Where the window is, kept as it changes: asking the window from inside its own closing
        # handler waits on the thread that is running that handler, and never returns.
        self._geometry = {k: geometry[k] for k in ("x", "y", "w", "h") if k in geometry}
        self._geometry.setdefault("w", w)
        self._geometry.setdefault("h", h)
        self.window.events.moved += lambda x, y: self._geometry.update(x=x, y=y)
        self.window.events.resized += lambda width, height: self._geometry.update(w=width, h=height)
        self.window.events.shown += self._round_corners
        self.window.events.closing += self.remember

    def run(self):
        # On Windows, Edge WebView2 or nothing: pywebview would otherwise fall back to Internet
        # Explorer's engine, which cannot run the page. The .exe carries the window's icon.
        webview.start(gui="edgechromium" if sys.platform == "win32" else None, private_mode=False)

    # what Api asks of its window
    def theme(self):
        return self._theme

    def push(self, kind, payload):
        script = f"window.fromApp && window.fromApp({json.dumps(kind)}, {json.dumps(payload)});"
        try:
            self.window.evaluate_js(script)
        except Exception:        # the window is closing, or not up yet; the page asks again
            pass

    def on_message(self, message):
        msg = json.loads(message)
        method, args, rid = msg["method"], msg.get("args", []), msg["id"]
        try:
            reply = {"id": rid, "result": getattr(self.api, method)(*args)}
        except Exception as exc:          # shown in the window, not swallowed
            reply = {"id": rid, "error": str(exc)}
        self.push("reply", reply)

    def begin(self, _what, _edge=None):
        pass                              # SHIM does dragging and resizing in the page

    def minimize(self):
        self.window.minimize()

    def toggle_maximize(self):
        self.window.restore() if self._maximized else self.window.maximize()
        self._maximized = not self._maximized

    def remember(self):
        if not self._maximized and "x" in self._geometry:
            self._remember(dict(self._geometry))

    def quit(self):
        self.window.destroy()

    def _round_corners(self):
        """Windows 11: ask the desktop to round this window's corners (DWMWA_WINDOW_CORNER_PREFERENCE
        = 33, DWMWCP_ROUND = 2). Windows 10 does not know the setting and nothing happens."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = self.window.native.Handle.ToInt32()
            pref = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception:
            pass
