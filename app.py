from flask import Flask, request, jsonify, send_from_directory, Response
import requests
import re
import urllib.parse
import html as html_module
import os
from bs4 import BeautifulSoup

app = Flask(__name__, static_folder='.', static_url_path='')

UA = {
    "chrome":     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "mobile":     "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "fbcrawler":  "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
}

def get_headers(ua_key):
    return {
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "User-Agent":      UA.get(ua_key, UA["chrome"]),
    }

CONTENT_PATTERNS = [
    r"facebook\.com/[^/?#]+/posts/\d+",
    r"facebook\.com/\d+/posts/\d+",
    r"facebook\.com/[^/?#]+/videos/\d+",
    r"facebook\.com/watch(?:/|\?v=)\d+",
    r"facebook\.com/reel/\d+",
    r"facebook\.com/photo(?:/|\?fbid=)\d+",
    r"facebook\.com/[^/?#]+/photos/\d+",
    r"facebook\.com/story\.php",
    r"facebook\.com/permalink\.php",
]

LOGIN_INDICATORS = ["login", "checkpoint", "recover", "about:blank"]

def is_login_url(url):
    return any(ind in url for ind in LOGIN_INDICATORS)

def is_content_url(url):
    if not url or is_login_url(url):
        return False
    return any(re.search(p, url) for p in CONTENT_PATTERNS)

def canonicalize(url):
    try:
        parsed = urllib.parse.urlparse(url)
        if "story.php" in parsed.path:
            qs = urllib.parse.parse_qs(parsed.query)
            uid  = qs.get("id",         [None])[0]
            fbid = qs.get("story_fbid", [None])[0]
            if uid and fbid:
                return f"https://www.facebook.com/{uid}/posts/{fbid}"
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    except:
        return url

def extract_next_param(url):
    try:
        parsed  = urllib.parse.urlparse(url)
        qs      = urllib.parse.parse_qs(parsed.query)
        next_url = qs.get("next", [None])[0]
        if next_url:
            decoded = urllib.parse.unquote(next_url)
            if decoded.startswith("http") and not is_login_url(decoded):
                return decoded
    except:
        pass
    return None

def find_content_url_in_html(raw_html):
    for pattern in CONTENT_PATTERNS:
        regex_str = r'https?://(?:www\.)?(' + pattern.replace(r'facebook\.com', r'facebook.com') + r'[^"\s]*)'
        match = re.search(regex_str, raw_html)
        if match:
            url = "https://www." + match.group(0).split("www.")[-1]
            if is_content_url(url):
                return url
    return None

def extract_page_id(url):
    try:
        path = urllib.parse.urlparse(url).path.strip("/")
        if "story.php" in url or "permalink.php" in url:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            return qs.get("id", [None])[0]
        segments = [s for s in path.split("/")
                    if s and s not in ("watch", "reel", "photo", "videos", "posts", "photos")]
        return segments[0] if segments else None
    except:
        return None

def is_accessible_image(session, url):
    """Check URL serves actual image bytes using GET+stream.
    Facebook CDN rejects HEAD requests so we use GET and close immediately."""
    if not url or "lookaside.fbsbx.com" in url:
        return False
    try:
        h = get_headers("chrome")
        h["Referer"] = "https://www.facebook.com/"
        r = session.get(url, headers=h, timeout=5,
                        allow_redirects=True, stream=True)
        ct = r.headers.get("Content-Type", "")
        r.close()
        return r.status_code == 200 and ct.startswith("image/")
    except:
        return False

def get_chrome_headers():
    """Full browser-like headers including client hints that Facebook checks."""
    return {
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "Cache-Control":             "max-age=0",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":            "document",
        "Sec-Fetch-Mode":            "navigate",
        "Sec-Fetch-Site":            "same-origin",
        "Sec-Fetch-User":            "?1",
        "sec-ch-ua":                 '"Google Chrome";v="124", "Not.A/Brand";v="8", "Chromium";v="124"',
        "sec-ch-ua-mobile":          "?0",
        "sec-ch-ua-platform":        '"Windows"',
        "User-Agent":                UA["chrome"],
    }


def seed_session_cookies(session):
    """Visit facebook.com homepage to pick up datr/sb cookies.
    Facebook requires the datr cookie to serve full page HTML with images.
    Without it, posts return a stripped response."""
    try:
        session.get("https://www.facebook.com/",
                    headers=get_chrome_headers(), timeout=8)
    except Exception as e:
        print(f"  cookie seed error: {e}")


def fetch_metadata(method, canonical_url, session):
    title = description = image = avatar = None
    try:
        # Pass 1: fbcrawler UA — reliably gets og:title and og:description
        # Facebook serves these to its own crawler without requiring login.
        resp_crawler = session.get(canonical_url,
                                   headers=get_headers("fbcrawler"), timeout=8)
        soup_crawler = BeautifulSoup(resp_crawler.text, "html.parser")

        og_title = (soup_crawler.find("meta", property="og:title") or
                    soup_crawler.find("meta", attrs={"name": "og:title"}))
        title = og_title["content"] if og_title else None
        if not title:
            t = soup_crawler.find("title")
            title = t.text if t else None
        if title:
            title = html_module.unescape(title).strip()
            for suffix in [" | Facebook", " - Facebook"]:
                if title.endswith(suffix):
                    title = title[:-len(suffix)].strip()

        og_desc = (soup_crawler.find("meta", property="og:description") or
                   soup_crawler.find("meta", attrs={"name": "og:description"}))
        description = og_desc["content"] if og_desc else None
        if description:
            description = html_module.unescape(description).strip()

        # Pass 2: Chrome UA + datr cookie — gets full page HTML with images.
        # Seed session with facebook.com visit first to get the datr cookie,
        # which Facebook requires to serve complete page HTML to Chrome UA.
        seed_session_cookies(session)

        resp_chrome = session.get(canonical_url,
                                  headers=get_chrome_headers(), timeout=10)
        raw_html    = resp_chrome.text

        # Strategy A: <link rel="preload" as="image"> — the primary image
        # Facebook declares this on line ~148, it's what WhatsApp uses too.
        preload_match = re.search(
            r'<link[^>]+rel="preload"[^>]+as="image"[^>]+href="([^"]+)"'
            r'|<link[^>]+href="([^"]+)"[^>]+as="image"[^>]+rel="preload"',
            raw_html, re.IGNORECASE
        )

        if preload_match:
            preload_url = html_module.unescape(
                preload_match.group(1) or preload_match.group(2) or ""
            )

            # 🔥 only accept REAL post images
            if (
                preload_url
                and "scontent" in preload_url
                and "fbcdn.net" in preload_url
                and "t39.30808-6" in preload_url
            ):
                image = preload_url

        # Strategy B: og:image from Chrome response (scontent CDN)
        if not image:
            soup_chrome = BeautifulSoup(raw_html, "html.parser")
    
            # 1. og:image:secure_url (best)
            secure = soup_chrome.find("meta", property="og:image:secure_url")
            if secure and secure.get("content"):
                url = html_module.unescape(secure["content"])
                if (
                    "scontent" in url and
                    "fbcdn.net" in url and
                    "lookaside.fbsbx.com" not in url
                ):
                    image = url
    
            # 2. fallback to og:image
            if not image:
                og_img = (
                    soup_chrome.find("meta", property="og:image") or
                    soup_chrome.find("meta", attrs={"name": "og:image"})
                )
                if og_img and og_img.get("content"):
                    url = html_module.unescape(og_img["content"])
                    if (
                        "scontent" in url and
                        "fbcdn.net" in url and
                        "lookaside.fbsbx.com" not in url
                    ):
                        image = url

        # Avatar: profile picture bucket (t39.30808-1)
        if not hasattr(resp_chrome, '_soup'):
            soup_chrome = BeautifulSoup(raw_html, "html.parser")
        for img_tag in soup_chrome.find_all("img", src=True):
            src = html_module.unescape(img_tag["src"])
            if re.search(r'scontent.*fbcdn\.net/v/t39\.30808-1/', src):
                if is_accessible_image(session, src):
                    avatar = src
                    break

    except Exception as e:
        print(f"fetch_metadata error: {e}")

    return {
        "status":      "success",
        "method":      method,
        "url":         canonical_url,
        "title":       title,
        "description": description,
        "page_id":     extract_page_id(canonical_url),
        "avatar":      avatar,   # profile pic (t39.30808-1) or None
        "image":       image,    # post thumbnail (t39.30808-6) or None
    }


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/proxy-image")
def proxy_image():
    url = request.args.get("url", "")
    if not url:
        return "", 400
    # Block lookaside — we know it won't work
    if "lookaside.fbsbx.com" in url:
        return "", 404
    try:
        h = get_headers("chrome")
        h["Referer"]         = "https://www.facebook.com/"
        h["Origin"]          = "https://www.facebook.com"
        h["Sec-Fetch-Site"]  = "cross-site"   # correct — we ARE cross-site
        h["Sec-Fetch-Mode"]  = "no-cors"
        h["Sec-Fetch-Dest"]  = "image"
        resp = requests.get(url, headers=h, timeout=10,
                            stream=True, allow_redirects=True)
        if resp.status_code != 200:
            return "", resp.status_code
        ct = resp.headers.get("Content-Type", "image/jpeg")
        if not ct.startswith("image/"):
            return "", 404   # got HTML error page, not an image
        return Response(resp.iter_content(chunk_size=8192), content_type=ct)
    except Exception as e:
        print(f"proxy error: {e}")
        return "", 404


@app.route("/resolve", methods=["POST"])
def resolve_link():
    data = request.json
    if not data or "url" not in data:
        return jsonify({"status": "failed", "message": "Missing url"}), 400

    share_url = data["url"]
    session   = requests.Session()

    print(f"\n{'─'*55}")
    print(f"  Resolving: {share_url}")
    print(f"{'─'*55}")

    # ── STRATEGY 1 ── hop-by-hop, hunt for login?next= ──────
    print("Strategy 1 - Manual redirect tracing")
    try:
        current = share_url
        for hop in range(8):
            resp = session.get(current, headers=get_headers("chrome"),
                               allow_redirects=False, timeout=10)
            loc = resp.headers.get("location", "")
            print(f"  hop {hop+1}: HTTP {resp.status_code} -> {current[:80]}")
            if loc:
                if loc.startswith("/"):
                    p = urllib.parse.urlparse(current)
                    loc = f"{p.scheme}://{p.netloc}{loc}"
                real = extract_next_param(loc)
                if real:
                    print(f"  [+] login?next= -> {real}")
                    return jsonify(fetch_metadata("login_next_param", canonicalize(real), session))
                if is_content_url(loc):
                    print(f"  [+] Location is content URL")
                    return jsonify(fetch_metadata("redirect_location", canonicalize(loc), session))
                current = loc
            else:
                break
    except Exception as e:
        print(f"  Strat 1 error: {e}")

    # ── STRATEGY 2 ── full GET + HTML parsing ───────────────
    print("Strategy 2 - HTML parsing")
    for ua in ["chrome", "mobile"]:
        try:
            resp      = session.get(share_url, headers=get_headers(ua), timeout=12)
            raw_html  = resp.text
            final_url = resp.url

            real = extract_next_param(final_url)
            if real:
                return jsonify(fetch_metadata("final_url_next_param", canonicalize(real), session))

            soup = BeautifulSoup(raw_html, "html.parser")

            og_url = (soup.find("meta", property="og:url") or
                      soup.find("meta", attrs={"name": "og:url"}))
            if og_url and is_content_url(og_url.get("content", "")):
                return jsonify(fetch_metadata("og_url", canonicalize(og_url["content"]), session))

            canon = soup.find("link", rel="canonical")
            if canon and is_content_url(canon.get("href", "")):
                return jsonify(fetch_metadata("canonical_link", canonicalize(canon["href"]), session))

            nxt = soup.find("input", attrs={"name": "next"})
            if nxt and nxt.get("value"):
                form_real = urllib.parse.unquote(nxt["value"])
                if is_content_url(form_real):
                    return jsonify(fetch_metadata("form_hidden_next", canonicalize(form_real), session))

            found = find_content_url_in_html(raw_html)
            if found:
                return jsonify(fetch_metadata("html_pattern", canonicalize(found), session))

        except Exception as e:
            print(f"  Strat 2 error [{ua}]: {e}")

    # ── STRATEGY 3 ── facebookexternalhit UA ────────────────
    print("Strategy 3 - fbcrawler")
    try:
        resp     = session.get(share_url, headers=get_headers("fbcrawler"), timeout=12)
        raw_html = resp.text
        req_url  = resp.url

        soup   = BeautifulSoup(raw_html, "html.parser")
        og_url = (soup.find("meta", property="og:url") or
                  soup.find("meta", attrs={"name": "og:url"}))
        if og_url and is_content_url(og_url.get("content", "")):
            return jsonify(fetch_metadata("fbcrawler_og_url", canonicalize(og_url["content"]), session))
        if is_content_url(req_url):
            return jsonify(fetch_metadata("fbcrawler_redirect", canonicalize(req_url), session))
        real = extract_next_param(req_url)
        if real:
            return jsonify(fetch_metadata("fbcrawler_next_param", canonicalize(real), session))
    except Exception as e:
        print(f"  Strat 3 error: {e}")

    # ── STRATEGY 4 ── mbasic.facebook.com ───────────────────
    print("Strategy 4 - mbasic")
    try:
        mbasic = re.sub(r"(www\.|m\.)?facebook\.com", "mbasic.facebook.com", share_url)
        resp   = session.get(mbasic, headers=get_headers("mobile"), timeout=12)
        raw_html = resp.text
        for pat in [
            r'href="(https?://mbasic\.facebook\.com/story\.php[^"]+)"',
            r'href="(https?://(?:www|m)\.facebook\.com/[^"]*(?:posts|videos|reel|watch|photo)[^"]*)"',
            r'href="(/story\.php[^"]+)"',
        ]:
            m = re.search(pat, raw_html)
            if m:
                found = urllib.parse.unquote(m.group(1))
                if found.startswith("/"):
                    found = f"https://www.facebook.com{found}"
                found = found.replace("mbasic.facebook.com", "www.facebook.com")
                if is_content_url(found) or "story.php" in found:
                    return jsonify(fetch_metadata("mbasic", canonicalize(found), session))
    except Exception as e:
        print(f"  Strat 4 error: {e}")

    return jsonify({
        "status":  "failed",
        "message": "Could not resolve — post may be private, deleted, or login-walled.",
    }), 400


if __name__ == "__main__":
    app.run(debug=True, port=5000)