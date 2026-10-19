"""Create the card pages app/c/0/ ... app/c/9/ that WhatsApp links point to.

WhatsApp reads each page's og:image tag and shows the stamp card as a large picture
in the chat. Run again if the web address or the card wording changes:

    python make_card_pages.py [https://your-address]
"""
import html
import sys
from pathlib import Path

BASE_URL = "https://happy-flower-011ac9603.1.azurestaticapps.net"
BUSINESS = "Mighty Car-Wash"
LOCATION = "Bolobedu Matipane Molai"
PAID = 9          # 9 paid washes, the 10th is free
VERSION = "1"     # bump when the card design changes, so WhatsApp fetches the new picture

OUT = Path(__file__).parent / "app" / "c"


def status(n):
    if n == 0:
        return "Your new loyalty card has started. Every 10th wash is a Free Wash."
    if n >= PAID:
        return "Your card is full. Your next wash is a FREE WASH!"
    return "%d of %d washes done. %d more to go until your Free Wash." % (n, PAID, PAID - n)


def page(n, base):
    img = "%s/cards/card-%d.png?v=%s" % (base, n, VERSION)
    title = "%s loyalty card" % BUSINESS
    desc = status(n)
    e = html.escape
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{e(BUSINESS)}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{base}/c/{n}/">
<meta property="og:image" content="{img}">
<meta property="og:image:secure_url" content="{img}">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="675">
<meta property="og:image:alt" content="{e(BUSINESS)} stamp card: {e(desc)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#05090f">
<link rel="icon" href="/icon-192.png">
<style>
  :root{{color-scheme:dark}}
  body{{margin:0;background:#05090f;color:#e8f1f6;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.5}}
  main{{max-width:640px;margin:0 auto;padding:24px 16px 40px;display:grid;gap:16px}}
  img{{width:100%;max-width:100%;border-radius:14px;display:block}}
  h1{{margin:0;font-size:22px;line-height:1.25}}
  p{{margin:0;color:#8aa1b1}}
</style>
</head>
<body>
<main>
  <img src="/cards/card-{n}.png?v={VERSION}" alt="{e(BUSINESS)} stamp card">
  <h1>{e(desc)}</h1>
  <p>Give your cell number at the counter on every visit to collect your stamps.</p>
  <p>{e(BUSINESS)} · {e(LOCATION)}</p>
</main>
</body>
</html>
"""


def main():
    base = (sys.argv[1] if len(sys.argv) > 1 else BASE_URL).rstrip("/")
    for n in range(PAID + 1):
        d = OUT / str(n)
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(page(n, base), encoding="utf-8", newline="\n")
    print("wrote %d card pages for %s" % (PAID + 1, base))


if __name__ == "__main__":
    main()
