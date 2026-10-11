#!/usr/bin/env python3
"""
Riepilogo Telegram di tutte le occasioni (lanciato ogni 4 ore).

Occasione = offerta CardTrader Zero inglese sotto il prezzo Cardmarket;
la giapponese conta solo se non c'e' nessuna offerta inglese.
Ordine: dal risparmio piu' alto al piu' basso.
"""

import html
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES_FILE = os.path.join(ROOT, "data", "prices.json")
PAGE_URL = os.environ.get("PAGE_URL", "https://stefanoallegro11-code.github.io/mtg-prices/")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
MAX_LEN = 3800  # limite Telegram 4096 caratteri per messaggio


def send(text):
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
        data=json.dumps({"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
                         "disable_web_page_preview": True}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read().decode())
    if not res.get("ok"):
        raise RuntimeError(res)


def deals(cards):
    out = []
    for c in cards:
        cm = c.get("cm")
        en, jp = c.get("en"), c.get("jp")
        offer, lang = (en, "EN") if en else ((jp, "JP") if jp else (None, None))
        if cm is None or offer is None or offer["price"] >= cm:
            continue
        sv = round(cm - offer["price"], 2)
        out.append((sv, c, offer, lang))
    out.sort(key=lambda x: -x[0])
    return out


def main():
    if not (TG_TOKEN and TG_CHAT):
        print("Mancano i secret TELEGRAM_TOKEN e/o TELEGRAM_CHAT_ID: nessun messaggio inviato.")
        sys.exit(1)
    with open(PRICES_FILE, encoding="utf-8") as f:
        data = json.load(f)
    rome = ZoneInfo("Europe/Rome")
    upd = datetime.fromisoformat(data["updated"]).astimezone(rome)
    ds = deals(data.get("cards", []))

    head = "🔥 <b>%d occasioni su CardTrader Zero</b>\nPrezzi delle %s" % (len(ds), upd.strftime("%H:%M del %d/%m"))
    if not ds:
        send(head + "\n\nNessuna carta sotto il prezzo Cardmarket in questo momento.\n\n" + PAGE_URL)
        return

    lines = []
    for i, (sv, c, o, lang) in enumerate(ds, 1):
        pct = round(sv / c["cm"] * 100)
        name = html.escape(c["name"])
        link = "https://www.cardtrader.com/it/cards/%s" % c["bp"] if c.get("bp") else None
        title = '<a href="%s">%s</a>' % (link, name) if link else name
        lines.append("%d. %s (%s)%s\n    CM €%.2f → %s €%.2f · <b>−€%.2f</b> (%d%%) · %s" % (
            i, title, c["set"].upper(), " ✨" if c.get("foil") else "",
            c["cm"], lang, o["price"], sv, pct, html.escape(o.get("cond") or "")))
    foot = "\n📊 Tutte le carte: " + PAGE_URL

    # Divide in piu' messaggi se troppo lungo
    msgs, cur = [], head + "\n"
    for ln in lines:
        if len(cur) + len(ln) + 2 > MAX_LEN:
            msgs.append(cur)
            cur = ""
        cur += "\n" + ln
    cur += "\n" + foot
    msgs.append(cur)
    for m in msgs:
        send(m)
    print("Inviati %d messaggi con %d occasioni" % (len(msgs), len(ds)))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Errore Telegram:", e)
        sys.exit(1)
