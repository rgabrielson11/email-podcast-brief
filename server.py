#!/usr/bin/env python3
import base64, json, logging, os, hashlib
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from urllib.parse import parse_qs, urlparse

DATA_DIR = Path("/data")
PORT = int(os.environ.get("HTTP_PORT", 8080))
PODCAST_TITLE  = os.environ.get("PODCAST_TITLE", "My Podcast")
PODCAST_DESC   = os.environ.get("PODCAST_DESC", "")
FEED_BASE_URL  = os.environ.get("FEED_BASE_URL", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
FEED_USER      = os.environ.get("FEED_USER", "podcast")
FEED_PASSWORD  = os.environ.get("FEED_PASSWORD", "")
STATE_FILE     = DATA_DIR / "processed.json"
AUDIO_OUT_DIR  = Path(os.environ["AUDIO_OUT_DIR"]) if os.environ.get("AUDIO_OUT_DIR") else DATA_DIR / "audio"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [HTTP] %(message)s")
log = logging.getLogger(__name__)

# ── Basic Auth ─────────────────────────────────────────────────────────────
def check_basic_auth(headers) -> bool:
    if not FEED_PASSWORD:
        return True
    auth = headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
        user, _, pw = decoded.partition(":")
        return user == FEED_USER and pw == FEED_PASSWORD
    except Exception:
        return False

def send_401(handler):
    handler.send_response(401)
    handler.send_header("WWW-Authenticate", f'Basic realm="{PODCAST_TITLE}"')
    handler.send_header("Content-Length", "0")
    handler.end_headers()

# ── Admin session store ────────────────────────────────────────────────────
_sessions = set()

def make_token(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()

def is_admin(cookie_header: str) -> bool:
    if not cookie_header:
        return False
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip() == "auth" and v.strip() in _sessions:
            return True
    return False

# ── Data ───────────────────────────────────────────────────────────────────
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"processed": [], "episodes": []}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def load_episodes():
    return sorted(load_state().get("episodes", []), key=lambda x: x["timestamp"], reverse=True)

def delete_episode(uid_hash: str) -> bool:
    state = load_state()
    ep = next((e for e in state["episodes"] if e["uid_hash"] == uid_hash), None)
    if not ep:
        return False
    state["episodes"] = [e for e in state["episodes"] if e["uid_hash"] != uid_hash]
    for d in [AUDIO_OUT_DIR, DATA_DIR / "audio"]:
        f = d / ep["filename"]
        if f.exists():
            f.unlink()
            log.info(f"Deleted {f}")
    save_state(state)
    log.info(f"Removed episode: {ep['subject']}")
    return True

# ── HTML ───────────────────────────────────────────────────────────────────
STYLE = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       background: #111; color: #eee; min-height: 100vh; }
header { background: #1a1a1a; border-bottom: 2px solid #c8a84b;
         padding: 24px 32px; display: flex; align-items: center; gap: 24px; }
header img { width: 80px; height: 80px; object-fit: contain; border-radius: 8px; }
header h1 { font-size: 1.6rem; font-weight: 700; color: #fff; }
header p  { color: #aaa; margin-top: 4px; font-size: 0.9rem; }
.hdr-right { margin-left: auto; display: flex; gap: 10px; align-items: center; }
.btn { background: #c8a84b; color: #111; padding: 8px 16px; border-radius: 6px;
       text-decoration: none; font-weight: 600; font-size: 0.85rem;
       white-space: nowrap; border: none; cursor: pointer; }
.btn-ghost { background: transparent; border: 1px solid #444; color: #aaa; }
.btn-red   { background: #b03030; color: #fff; }
.btn-red:hover { background: #c03838; }
main { max-width: 800px; margin: 32px auto; padding: 0 16px; }
.episode { background: #1e1e1e; border: 1px solid #2a2a2a; border-radius: 10px;
           padding: 20px; margin-bottom: 16px; }
.ep-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.ep-info { flex: 1; }
.ep-meta { font-size: 0.78rem; color: #888; margin-bottom: 6px; }
.ep-title { font-size: 1.05rem; font-weight: 600; color: #fff;
            margin-bottom: 14px; line-height: 1.4; }
audio { width: 100%; height: 40px; margin-bottom: 10px; accent-color: #c8a84b; }
a.dl { font-size: 0.8rem; color: #c8a84b; text-decoration: none; }
a.dl:hover { text-decoration: underline; }
.none { color: #666; text-align: center; padding: 48px 0; }
footer { text-align: center; color: #444; font-size: 0.75rem; padding: 32px; }
.login-wrap { max-width: 340px; margin: 80px auto; background: #1e1e1e;
              border: 1px solid #2a2a2a; border-radius: 12px; padding: 32px; }
.login-wrap h2 { margin-bottom: 20px; color: #c8a84b; }
.login-wrap input { width: 100%; padding: 10px 14px; background: #111;
                    border: 1px solid #333; border-radius: 6px; color: #eee;
                    font-size: 1rem; margin-bottom: 14px; }
.flash { background: #3a1a1a; color: #f88; border-radius: 6px;
         padding: 10px 14px; margin-bottom: 14px; font-size: 0.875rem; }
.flash.ok { background: #1a3a1a; color: #8f8; }
"""

def page(body: str) -> bytes:
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{PODCAST_TITLE}</title><style>{STYLE}</style>
</head><body>{body}</body></html>""".encode("utf-8")

def render_player(admin: bool, flash: str = "", flash_ok: bool = False) -> bytes:
    episodes = load_episodes()
    feed_url = f"{FEED_BASE_URL}/feed.xml" if FEED_BASE_URL else "/feed.xml"
    # Build credentialed subscribe URL for display
    if FEED_PASSWORD and FEED_BASE_URL:
        sub_url = FEED_BASE_URL.replace("://", f"://{FEED_USER}:{FEED_PASSWORD}@") + "/feed.xml"
    else:
        sub_url = feed_url

    right = f'<a class="btn" href="{sub_url}">Copy Subscribe URL</a>'
    if admin:
        right += '<a class="btn btn-ghost" href="/logout">Log out</a>'
    else:
        right += '<a class="btn btn-ghost" href="/login">Manage</a>'

    flash_html = f'<div class="flash{"  ok" if flash_ok else ""}">{flash}</div>' if flash else ""

    ep_html = ""
    for ep in episodes:
        try:
            dt = datetime.fromisoformat(ep["timestamp"])
            date_str = dt.strftime("%B %d, %Y")
        except Exception:
            date_str = ep.get("timestamp", "")[:10]
        size_mb = round(ep.get("filesize", 0) / 1024 / 1024, 1)
        audio_url = f"{FEED_BASE_URL}/audio/{ep['filename']}" if FEED_BASE_URL else f"/audio/{ep['filename']}"
        delete_btn = ""
        if admin:
            delete_btn = f"""<form style="display:inline" method="POST" action="/delete"
              onsubmit="return confirm('Delete this episode?')">
              <input type="hidden" name="uid_hash" value="{ep['uid_hash']}">
              <button class="btn btn-red" type="submit">Delete</button>
            </form>"""
        ep_html += f"""
        <div class="episode">
          <div class="ep-top">
            <div class="ep-info">
              <div class="ep-meta">{date_str} &nbsp;·&nbsp; {size_mb} MB</div>
              <div class="ep-title">{ep['subject']}</div>
            </div>{delete_btn}
          </div>
          <audio controls preload="none">
            <source src="{audio_url}" type="audio/mpeg">
          </audio>
          <a class="dl" href="{audio_url}" download>⬇ Download</a>
        </div>"""

    if not ep_html:
        ep_html = '<p class="none">No episodes yet.</p>'

    if FEED_PASSWORD and FEED_BASE_URL:
        sub_url = FEED_BASE_URL.replace("://", f"://{FEED_USER}:{FEED_PASSWORD}@") + "/feed.xml"
    else:
        sub_url = f"{FEED_BASE_URL}/feed.xml" if FEED_BASE_URL else "/feed.xml"

    qr_html = f"""
<div id="qr-section" style="background:#1a1a1a;border-bottom:1px solid #2a2a2a;padding:20px 32px;display:flex;align-items:center;gap:32px;">
  <div>
    <div style="font-size:0.8rem;color:#888;margin-bottom:6px;">SUBSCRIBE IN OVERCAST / PODCAST APP</div>
    <div style="font-family:monospace;font-size:0.8rem;color:#c8a84b;word-break:break-all;max-width:420px;">{sub_url}</div>
    <button onclick="navigator.clipboard.writeText('{sub_url}').then(()=>{{this.textContent='Copied!';setTimeout(()=>this.textContent='Copy URL',2000)}})"
      style="margin-top:10px;background:#2a2a2a;border:1px solid #444;color:#eee;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:0.8rem;">Copy URL</button>
  </div>
  <div style="flex-shrink:0;">
    <div style="font-size:0.75rem;color:#888;margin-bottom:8px;text-align:center;">SCAN TO SUBSCRIBE</div>
    <div id="qrcode" style="background:white;padding:8px;border-radius:8px;display:inline-block;"></div>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>
  new QRCode(document.getElementById("qrcode"), {{
    text: "{sub_url}",
    width: 120, height: 120,
    colorDark: "#000000", colorLight: "#ffffff",
    correctLevel: QRCode.CorrectLevel.M
  }});
</script>"""

    body = f"""
<header>
  <img src="{FEED_BASE_URL}/cover.png" alt="Cover">
  <div><h1>{PODCAST_TITLE}</h1><p>{PODCAST_DESC}</p></div>
  <div class="hdr-right">{right}</div>
</header>
{qr_html}<main>{flash_html}{ep_html}</main>
<footer><a href="{feed_url}" style="color:#555">RSS Feed</a></footer>"""
    return page(body)

def render_login(error: str = "") -> bytes:
    err_html = f'<div class="flash">{error}</div>' if error else ""
    if FEED_PASSWORD and FEED_BASE_URL:
        sub_url = FEED_BASE_URL.replace("://", f"://{FEED_USER}:{FEED_PASSWORD}@") + "/feed.xml"
    else:
        sub_url = f"{FEED_BASE_URL}/feed.xml" if FEED_BASE_URL else "/feed.xml"

    qr_html = f"""
<div id="qr-section" style="background:#1a1a1a;border-bottom:1px solid #2a2a2a;padding:20px 32px;display:flex;align-items:center;gap:32px;">
  <div>
    <div style="font-size:0.8rem;color:#888;margin-bottom:6px;">SUBSCRIBE IN OVERCAST / PODCAST APP</div>
    <div style="font-family:monospace;font-size:0.8rem;color:#c8a84b;word-break:break-all;max-width:420px;">{sub_url}</div>
    <button onclick="navigator.clipboard.writeText('{sub_url}').then(()=>{{this.textContent='Copied!';setTimeout(()=>this.textContent='Copy URL',2000)}})"
      style="margin-top:10px;background:#2a2a2a;border:1px solid #444;color:#eee;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:0.8rem;">Copy URL</button>
  </div>
  <div style="flex-shrink:0;">
    <div style="font-size:0.75rem;color:#888;margin-bottom:8px;text-align:center;">SCAN TO SUBSCRIBE</div>
    <div id="qrcode" style="background:white;padding:8px;border-radius:8px;display:inline-block;"></div>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>
  new QRCode(document.getElementById("qrcode"), {{
    text: "{sub_url}",
    width: 120, height: 120,
    colorDark: "#000000", colorLight: "#ffffff",
    correctLevel: QRCode.CorrectLevel.M
  }});
</script>"""

    body = f"""<div class="login-wrap">
  <h2>🔒 Manage Podcast</h2>{err_html}
  <form method="POST" action="/login">
    <input type="password" name="password" placeholder="Admin password" autofocus>
    <button class="btn" type="submit" style="width:100%">Sign in</button>
  </form></div>"""
    return page(body)

# ── Handler ────────────────────────────────────────────────────────────────
class PodcastHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        log.info(format % args)

    def send_html(self, data: bytes, status: int = 200, extra_headers: dict = None, skip_body: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        if not skip_body:
            self.wfile.write(data)

    def send_file(self, path: Path, content_type: str, skip_body: bool = False):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not skip_body:
            self.wfile.write(data)

    def redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def read_body(self) -> str:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length).decode("utf-8") if length else ""

    def do_HEAD(self):
        """Handle HEAD requests (used by podcast clients and curl -I)."""
        self.do_GET(skip_body=True)

    def do_GET(self, skip_body=False):
        path = self.path.split("?")[0]

        # All routes require Basic Auth if FEED_PASSWORD is set
        if not check_basic_auth(self.headers):
            send_401(self)
            return

        admin = is_admin(self.headers.get("Cookie", ""))

        if path in ("/", "/player"):
            self.send_html(render_player(admin))
        elif path == "/login":
            self.send_html(render_login())
        elif path == "/logout":
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k.strip() == "auth":
                    _sessions.discard(v.strip())
            self.redirect("/")
        elif path == "/feed.xml":
            f = DATA_DIR / "feed.xml"
            if f.exists():
                self.send_file(f, "application/rss+xml")
            else:
                self.send_response(404); self.end_headers()
        elif path == "/cover.png":
            f = DATA_DIR / "cover.png"
            if f.exists():
                self.send_file(f, "image/png")
            else:
                self.send_response(404); self.end_headers()
        elif path.startswith("/audio/"):
            f = DATA_DIR / path.lstrip("/")
            if f.exists() and f.suffix == ".mp3":
                self.send_file(f, "audio/mpeg")
            else:
                self.send_response(404); self.end_headers()
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]

        if not check_basic_auth(self.headers):
            send_401(self)
            return

        body = self.read_body()
        params = parse_qs(body)

        if path == "/login":
            pw = params.get("password", [""])[0]
            if pw == ADMIN_PASSWORD:
                token = make_token(pw + os.urandom(16).hex())
                _sessions.add(token)
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"auth={token}; Path=/; HttpOnly; SameSite=Lax")
                self.end_headers()
            else:
                self.send_html(render_login("Incorrect password"), status=401)
        elif path == "/delete":
            if not is_admin(self.headers.get("Cookie", "")):
                self.redirect("/login"); return
            uid_hash = params.get("uid_hash", [""])[0]
            ok = delete_episode(uid_hash)
            flash = "Episode deleted." if ok else "Episode not found."
            self.send_html(render_player(True, flash, flash_ok=ok))
        else:
            self.send_response(404); self.end_headers()

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), PodcastHandler)
    log.info(f"Serving on :{PORT} — player at /  feed at /feed.xml")
    server.serve_forever()
