"""obs-follower-goals helper: a tiny local service that collects your follower counts.

Serves  http://127.0.0.1:8765/counts  ->
    {"twitch": {"count": 19, "ok": true, "err": null, "at": 1763460000}, "youtube": {...}, "tiktok": {...}, "facebook": {...}}

The OBS widget (widget/goals.html) polls that URL and turns the numbers into follower goals.
Settings live in a plain-text env file (default: ~/.env.followers); see .env.example.
Only the Python standard library is used. A Chromium browser (Edge / Chrome / Chromium) is needed for TikTok and
for Facebook pages without a Graph API token.

    python server.py                    run the service
    python server.py --once             fetch every platform once, print the result and exit (good for testing)
    python server.py --twitch-login     one-time Twitch device login (only if you registered your own Twitch app)
    python server.py --env PATH --port 8765 --host 127.0.0.1
"""
import argparse, json, os, re, shutil, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__version__ = "1.0.0"
HOME = os.path.expanduser("~")
DEFAULT_ENV = os.environ.get("FOLLOWER_GOALS_ENV", os.path.join(HOME, ".env.followers"))
TOKEN_PATH = os.path.join(HOME, ".twitch-token.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PLATFORMS = ("facebook", "twitch", "tiktok", "youtube")
ENV_PATH = DEFAULT_ENV


def load_env():
    env = {}
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None,
                                 headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8", "replace")


# ---------------- headless browser (for sites that block plain HTTP clients) ----------------
def find_browser(env):
    if env.get("BROWSER_PATH") and os.path.exists(env["BROWSER_PATH"]):
        return env["BROWSER_PATH"]
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    for name in ("microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    return None


def browser_dom(env, url):
    exe = find_browser(env)
    if not exe:
        raise RuntimeError("no Chrome/Edge/Chromium found; set BROWSER_PATH in the env file")
    out = subprocess.run([exe, "--headless=new", "--disable-gpu", "--virtual-time-budget=10000", "--dump-dom", url],
                         stdin=subprocess.DEVNULL, capture_output=True, timeout=60,   # pythonw has no console stdin
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out.stdout.decode("utf-8", "replace")


def parse_followers(html):
    """'1,182 Followers' / '12.5K followers' -> int"""
    m = re.search(r'([\d.,]+)\s*([KkMm]?)\s+[Ff]ollowers', html)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return int(n * {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1))


# ---------------- YouTube: Data API v3 key ----------------
def youtube(env):
    key, cid, handle = env.get("YOUTUBE_API_KEY"), env.get("YOUTUBE_CHANNEL_ID"), env.get("YOUTUBE_HANDLE")
    if not key or not (cid or handle):
        return None
    q = {"part": "statistics", "key": key}
    if cid:
        q["id"] = cid
    else:
        q["forHandle"] = handle if handle.startswith("@") else "@" + handle
    items = json.loads(http("https://www.googleapis.com/youtube/v3/channels?" + urllib.parse.urlencode(q))).get("items") or []
    return int(items[0]["statistics"]["subscriberCount"]) if items else None


# ---------------- Facebook page: Graph API token, or the public page ----------------
def facebook(env):
    pid, tok = env.get("FACEBOOK_PAGE_ID"), env.get("FACEBOOK_PAGE_TOKEN")
    if not pid:
        return None
    if not tok:   # no token: read "N followers" from the public page
        return parse_followers(browser_dom(env, "https://www.facebook.com/" + urllib.parse.quote(pid)))
    d = json.loads(http(f"https://graph.facebook.com/v21.0/{pid}?" +
                        urllib.parse.urlencode({"fields": "followers_count,fan_count", "access_token": tok})))
    return int(d.get("followers_count") or d.get("fan_count") or 0)


# ---------------- TikTok: public profile page (unofficial) ----------------
def tiktok(env):
    user = env.get("TIKTOK_USERNAME", "").lstrip("@")
    if not user:
        return None
    html = browser_dom(env, f"https://www.tiktok.com/@{urllib.parse.quote(user)}")
    m = re.search(r'"followerCount"\s*:\s*(\d+)', html)
    return int(m.group(1)) if m else parse_followers(html)


# ---------------- Twitch: DecAPI (no setup) or your own app via Helix ----------------
def twitch_tokens(env):
    if not os.path.exists(TOKEN_PATH):
        return None
    t = json.load(open(TOKEN_PATH))
    if t.get("expires_at", 0) < time.time() + 120:   # refresh
        d = json.loads(http("https://id.twitch.tv/oauth2/token", {"client_id": env["TWITCH_CLIENT_ID"],
                            "grant_type": "refresh_token", "refresh_token": t["refresh_token"]}))
        t = {"access_token": d["access_token"], "refresh_token": d.get("refresh_token", t["refresh_token"]),
             "expires_at": time.time() + d.get("expires_in", 3600)}
        json.dump(t, open(TOKEN_PATH, "w"))
    return t


def twitch(env):
    cid, login = env.get("TWITCH_CLIENT_ID"), env.get("TWITCH_LOGIN", "").lstrip("@")
    if not login:
        return None
    t = twitch_tokens(env) if cid else None
    if not t:   # no own Twitch app: DecAPI's public follow-count endpoint
        txt = http("https://decapi.me/twitch/followcount/" + urllib.parse.quote(login)).strip().replace(",", "")
        return int(txt) if txt.isdigit() else None
    h = {"Client-Id": cid, "Authorization": "Bearer " + t["access_token"]}
    users = json.loads(http("https://api.twitch.tv/helix/users?login=" + urllib.parse.quote(login), headers=h))["data"]
    if not users:
        return None
    d = json.loads(http("https://api.twitch.tv/helix/channels/followers?broadcaster_id=" + users[0]["id"], headers=h))
    return int(d["total"])


def twitch_login():
    env = load_env()
    cid = env.get("TWITCH_CLIENT_ID")
    if not cid:
        sys.exit("Set TWITCH_CLIENT_ID in " + ENV_PATH + " first.")
    d = json.loads(http("https://id.twitch.tv/oauth2/device", {"client_id": cid, "scopes": "moderator:read:followers"}))
    print(f"\nOpen {d['verification_uri']} and approve (code {d['user_code']}).\n")
    while True:
        time.sleep(d.get("interval", 5))
        try:
            tok = json.loads(http("https://id.twitch.tv/oauth2/token", {"client_id": cid, "device_code": d["device_code"],
                                  "scopes": "moderator:read:followers", "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}))
        except urllib.error.HTTPError as e:
            if "authorization_pending" in e.read().decode():
                continue
            raise
        json.dump({"access_token": tok["access_token"], "refresh_token": tok["refresh_token"],
                   "expires_at": time.time() + tok.get("expires_in", 3600)}, open(TOKEN_PATH, "w"))
        print("Twitch login saved to " + TOKEN_PATH)
        return


# ---------------- polling + HTTP ----------------
FETCHERS = {"facebook": facebook, "twitch": twitch, "tiktok": tiktok, "youtube": youtube}
STATE = {k: {"count": None, "ok": False, "err": "not configured"} for k in PLATFORMS}
LAST = {}


def every(env, name):
    """seconds between checks; browser-based platforms are slower, so they default to 5 minutes"""
    default = 300 if name in ("tiktok", "facebook") else 60
    try:
        return max(15, int(env.get(name.upper() + "_EVERY", default)))
    except ValueError:
        return default


def poll_once(force=False):
    env = load_env()
    for name, fn in FETCHERS.items():
        if not force and time.time() - LAST.get(name, 0) < every(env, name):
            continue
        LAST[name] = time.time()
        try:
            n = fn(env)
            if n is None:
                STATE[name].update(ok=STATE[name]["count"] is not None, err="not configured or not found")
            else:
                STATE[name] = {"count": n, "ok": True, "err": None, "at": int(time.time())}
        except Exception as e:   # keep the last good value on failure
            STATE[name].update(err=f"{type(e).__name__}: {e}"[:200])


def poll_forever():
    while True:
        poll_once()
        time.sleep(15)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/counts", "/counts/"):
            body, code = json.dumps(STATE).encode(), 200
        elif path == "/health":
            body, code = json.dumps({"ok": True, "version": __version__}).encode(), 200
        else:
            body, code = b'{"error":"try /counts"}', 404
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    global ENV_PATH
    ap = argparse.ArgumentParser(description="Local follower-count service for the obs-follower-goals widget.")
    ap.add_argument("--env", default=DEFAULT_ENV, help="settings file (default: %(default)s)")
    ap.add_argument("--host", default="127.0.0.1", help="interface to listen on (default: %(default)s)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("FOLLOWER_GOALS_PORT", 8765)), help="port (default: %(default)s)")
    ap.add_argument("--once", action="store_true", help="fetch once, print the counts and exit")
    ap.add_argument("--twitch-login", action="store_true", help="one-time Twitch device login (own Twitch app only)")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args()
    ENV_PATH = a.env
    if a.twitch_login:
        return twitch_login()
    if a.once:
        poll_once(force=True)
        print(json.dumps(STATE, indent=2))
        return
    threading.Thread(target=poll_forever, daemon=True).start()
    print(f"obs-follower-goals {__version__}: http://{a.host}:{a.port}/counts  (settings: {ENV_PATH})")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
