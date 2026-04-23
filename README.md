# 🔗 LinkPrecision

> Resolve Facebook share links to their clean, canonical URLs — with rich post previews.

LinkPrecision is a lightweight Flask web app that unwraps Facebook redirect URLs (`l.facebook.com/l.php?u=...`, share links, story permalinks) and returns the clean destination URL along with the post's title, description, and image — the same way WhatsApp and Telegram do it.

---

## ✨ Features

- **4-strategy resolver** — hop-by-hop redirect tracing, HTML parsing, fbcrawler UA, and mbasic fallback
- **Rich previews** — OG title, description, and post image using `<link rel="preload">` + `og:image` metadata (no guessing)
- **Profile avatar extraction** — pulls the page/profile picture from Facebook's CDN
- **Image proxy** — serves Facebook CDN images through the backend to bypass CORS
- **History** — resolved links saved locally in the browser
- **Dark mode** — persistent theme preference
- **Responsive UI** — works on desktop, tablet, and mobile with skeleton loading states

---

## 🖼️ How Image Preview Works

Earlier versions scanned `<img>` tags and used raw HTML regex — picking random thumbnails, often wrong. The current approach mirrors how WhatsApp/Discord/Telegram work:

1. **Primary:** `<link rel="preload" as="image">` — Facebook's declared main post image
2. **Fallback:** `og:image:secure_url` → `og:image` from page metadata
3. **Validation:** Only accepts URLs containing `scontent` + `fbcdn.net`, rejecting `lookaside.fbsbx.com` proxy/temp links

No guessing. Just trusting the right signals.

---

## 🚀 Quick Start

### 1. Clone

```bash
git clone https://github.com/YOUR_USERNAME/linkprecision.git
cd linkprecision
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run

```bash
python app.py
```

Open `http://localhost:5000` in your browser.

---

## 📦 Requirements

```
Flask
requests
beautifulsoup4
brotli
gunicorn
```

> `brotli` is required because Facebook responses use Brotli encoding (`br`). Without it, `resp.text` may return garbled content.

---

## 📁 Project Structure

```
linkprecision/
├── app.py          # Flask backend — resolver logic, image proxy
├── index.html      # Frontend — single-page UI
├── requirements.txt
└── README.md
```

---

## 🔍 How the Resolver Works

Facebook share links go through up to 4 strategies before giving up:

| Strategy | Method |
|---|---|
| 1 | Hop-by-hop redirect tracing — extracts `?next=` from login redirects |
| 2 | Full GET + HTML parsing — checks `og:url`, `canonical`, hidden form fields |
| 3 | `facebookexternalhit` UA — Facebook serves clean responses to its own crawler |
| 4 | `mbasic.facebook.com` — mobile fallback that bypasses login walls |

---

## ⚠️ Limitations

- Only resolves **Facebook** share/redirect links — not a general-purpose URL expander
- Private posts, deleted content, and login-walled pages will fail
- Query string stripping removes **all** params, not just tracking ones
- History is stored in browser `localStorage` — not synced across devices

---

## 📄 License

MIT
