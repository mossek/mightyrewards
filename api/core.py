"""Business logic for Mighty Car-Wash Rewards.

Kept free of Azure imports so it can be tested with an in-memory store.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

GOAL = 10            # the 10th wash is free
PAID = GOAL - 1      # 9 paid washes fill the card
SA_TZ = timezone(timedelta(hours=2))   # South Africa has no daylight saving
STAFF_TOKEN_HOURS = 12
ADMIN_TOKEN_HOURS = 2
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60

# WhatsApp message templates. These must be created with exactly this wording
# in Meta WhatsApp Manager (see README). {{n}} are the template variables.
TEMPLATES = {
    "loyalty_welcome": {
        "image": True,
        "body": "Hi {{1}} 🚗✨\n\nWelcome to Mighty Car-Wash Rewards! Your first wash is on your loyalty card. "
                "Every 10th wash is a Free Wash.\n\nJust give your cell number at the counter each visit. "
                "Reply STOP if you don't want these messages.",
    },
    "loyalty_stamp": {
        "image": True,
        "body": "Hi {{1}} 🚗✨\n\nYour Mighty Car-Wash loyalty card has been updated! "
                "You've done {{2}} of 9 washes.\n\nOnly {{3}} more to go until your Free Wash. 💦",
    },
    "loyalty_full": {
        "image": True,
        "body": "Hi {{1}} 🎉\n\nYour Mighty Car-Wash loyalty card is full! Your next wash is a Free Wash.\n\n"
                "Just give your cell number at the counter.",
    },
    "loyalty_redeemed": {
        "image": True,
        "body": "Enjoy your Free Wash, {{1}}! 🎉\n\nYour new Mighty Car-Wash loyalty card has started. "
                "Keep that shine coming!",
    },
    "car_ready": {
        "image": False,
        "body": "Hi {{1}}, your vehicle is sparkling and ready for collection at Mighty Car-Wash. 🚗✨",
    },
}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Conflict(Exception):
    """Raised by a store when a conditional write loses a race."""


def norm_phone(value):
    """082 123 4567 / +27 82 123 4567 / 27821234567 -> 27821234567, else None."""
    d = re.sub(r"\D", "", str(value or ""))
    if len(d) == 10 and d.startswith("0"):
        d = "27" + d[1:]
    return d if re.fullmatch(r"27[6-8]\d{8}", d) else None


def norm_plate(value):
    """'ca 123-456' -> 'CA123456'. Letters and digits only, at least one letter."""
    p = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    return p if 2 <= len(p) <= 10 and re.search(r"[A-Z]", p) else None


def cars_of(c):
    return [p for p in str(c.get("cars") or "").split(",") if p]


def sa_day(ts):
    return datetime.fromtimestamp(ts, SA_TZ).strftime("%Y-%m-%d")


def render(template, params):
    text = TEMPLATES[template]["body"]
    for i, p in enumerate(params, start=1):
        text = text.replace("{{%d}}" % i, str(p))
    return text


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_pin(pin, salt):
    return hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32).hex()


class Rewards:
    def __init__(self, store, sender, cfg, clock=time.time):
        self.store = store
        self.sender = sender            # None when WhatsApp sending is off
        self.cfg = cfg
        self.clock = clock
        if not cfg.get("token_secret") or len(cfg["token_secret"]) < 32:
            raise RuntimeError("TOKEN_SECRET must be set (32+ characters)")
        if not re.fullmatch(r"\d{6,}", cfg.get("admin_pin") or ""):
            raise RuntimeError("ADMIN_PIN must be set (6+ digits)")

    @classmethod
    def from_env(cls, store, sender):
        cfg = {
            "token_secret": os.environ.get("TOKEN_SECRET", ""),
            "admin_pin": os.environ.get("ADMIN_PIN", ""),
            "staff_pin": os.environ.get("STAFF_PIN", ""),
            "business": os.environ.get("BUSINESS_NAME", "Mighty Car-Wash"),
            "location": os.environ.get("BUSINESS_LOCATION", "Bolobedu Matipane Molai"),
            "public_url": os.environ.get("PUBLIC_URL", "").rstrip("/"),
            "review_url": os.environ.get("REVIEW_URL", ""),
            "chat_number": os.environ.get("CHAT_NUMBER", "078 667 7610"),
            "wa_verify_token": os.environ.get("WA_VERIFY_TOKEN", ""),
            "wa_app_secret": os.environ.get("WA_APP_SECRET", ""),
        }
        return cls(store, sender, cfg)

    # ---------- sessions ----------
    def _staff_pin_record(self):
        rec = self.store.get_setting("staff_pin")
        if rec:
            return rec
        initial = self.cfg.get("staff_pin") or ""
        if not re.fullmatch(r"\d{4}", initial):
            return None
        salt = "00" * 16
        return {"salt": salt, "hash": hash_pin(initial, salt), "v": 0}

    def _sign(self, payload):
        body = _b64(json.dumps(payload, separators=(",", ":")).encode())
        sig = _b64(hmac.new(self.cfg["token_secret"].encode(), body.encode(), hashlib.sha256).digest())
        return body + "." + sig

    def login(self, pin, ip):
        now = self.clock()
        key = "throttle-" + re.sub(r"[^0-9a-fA-F.:]", "", ip or "unknown")[:60].replace(":", "_")
        th = self.store.get_setting(key) or {"fails": 0, "until": 0}
        if th["until"] > now:
            raise ApiError(429, "Too many wrong PINs. Try again in %d minutes." % max(1, round((th["until"] - now) / 60)))
        pin = str(pin or "")
        role = None
        if hmac.compare_digest(pin, self.cfg["admin_pin"]):
            role, ver, hours = "admin", 0, ADMIN_TOKEN_HOURS
        else:
            rec = self._staff_pin_record()
            if rec and re.fullmatch(r"\d{4}", pin) and hmac.compare_digest(hash_pin(pin, rec["salt"]), rec["hash"]):
                role, ver, hours = "staff", rec["v"], STAFF_TOKEN_HOURS
        if not role:
            fails = th["fails"] + 1
            until = now + LOCK_SECONDS if fails >= MAX_FAILS else 0
            self.store.set_setting(key, {"fails": 0 if until else fails, "until": until})
            if until:
                raise ApiError(429, "Too many wrong PINs. Try again in 15 minutes.")
            raise ApiError(401, "Wrong PIN. Try again.")
        if th["fails"]:
            self.store.set_setting(key, {"fails": 0, "until": 0})
        token = self._sign({"r": role, "v": ver, "e": int(now + hours * 3600)})
        return {"token": token, "role": role, "expires": int(now + hours * 3600)}

    def require(self, token, role="staff"):
        try:
            body, sig = (token or "").split(".")
            good = _b64(hmac.new(self.cfg["token_secret"].encode(), body.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(sig, good):
                raise ValueError
            p = json.loads(_unb64(body))
        except Exception:
            raise ApiError(401, "Please enter the PIN again.")
        if p["e"] < self.clock():
            raise ApiError(401, "Session ended. Enter the PIN again.")
        if p["r"] == "staff":
            rec = self._staff_pin_record()
            if not rec or rec["v"] != p["v"]:
                raise ApiError(401, "The PIN was changed. Enter the new PIN.")
        if role == "admin" and p["r"] != "admin":
            raise ApiError(403, "Only the owner can do this.")
        return p["r"]

    def change_staff_pin(self, new_pin):
        if not re.fullmatch(r"\d{4}", str(new_pin or "")):
            raise ApiError(400, "The PIN must be exactly 4 numbers.")
        old = self._staff_pin_record()
        salt = secrets.token_hex(16)
        self.store.set_setting("staff_pin", {"salt": salt, "hash": hash_pin(new_pin, salt), "v": (old["v"] + 1) if old else 1})
        return {"ok": True}

    # ---------- info ----------
    def config(self, role):
        return {
            "business": self.cfg["business"],
            "location": self.cfg["location"],
            "goal": GOAL,
            "role": role,
            "autoWhatsApp": self.sender is not None,
            "reviewUrl": self.cfg["review_url"],
            "catalog": self.catalog(),
        }

    # ---------- wash types & staff (owner settings) ----------
    def catalog(self):
        return self.store.get_setting("catalog") or {"types": [], "staff": []}

    @staticmethod
    def _id(value):
        v = str(value or "")
        return v if re.fullmatch(r"[a-z0-9]{1,16}", v) else secrets.token_hex(4)

    @staticmethod
    def _label(value, what):
        v = re.sub(r"\s+", " ", str(value or "")).strip()
        if not 1 <= len(v) <= 30:
            raise ApiError(400, "Each %s needs a name of up to 30 letters." % what)
        return v

    def save_catalog(self, body):
        types, staff = body.get("types"), body.get("staff")
        if not isinstance(types, list) or not isinstance(staff, list) or len(types) > 20 or len(staff) > 30:
            raise ApiError(400, "Invalid wash types or staff list.")
        out_types = []
        for t in types:
            try:
                price = round(float(t.get("price")), 2)
                stamps = int(t.get("stamps", 1))
            except (TypeError, ValueError):
                raise ApiError(400, "Each wash type needs a price, like 120.")
            if not 0 <= price <= 100000:
                raise ApiError(400, "Prices must be between R0 and R100 000.")
            if not 1 <= stamps <= 3:
                raise ApiError(400, "A wash type can give 1, 2 or 3 stamps.")
            out_types.append({"id": self._id(t.get("id")), "name": self._label(t.get("name"), "wash type"),
                              "price": price, "stamps": stamps})
        out_staff = [{"id": self._id(s.get("id")), "name": self._label(s.get("name"), "staff member")} for s in staff]
        cat = {"types": out_types, "staff": out_staff}
        self.store.set_setting("catalog", cat)
        return cat

    def _visit_details(self, body, c):
        """Wash type, staff member and car for one visit, checked against the owner's lists."""
        cat = self.catalog()
        types = {t["id"]: t for t in cat["types"]}
        staff = {s["id"]: s for s in cat["staff"]}
        t = types.get(body.get("type"))
        s = staff.get(body.get("staff"))
        if types and not t:
            raise ApiError(400, "Choose the wash type.")
        if staff and not s:
            raise ApiError(400, "Choose who washed the car.")
        plate = None
        if body.get("plate"):
            plate = norm_plate(body.get("plate"))
            if not plate or plate not in cars_of(c):
                raise ApiError(400, "That car isn't on this customer's profile. Add it first.")
        elif len(cars_of(c)) == 1:
            plate = cars_of(c)[0]
        return {
            "washType": t["name"] if t else "",
            "price": t["price"] if t else 0,
            "stamps": t["stamps"] if t else 1,
            "staff": s["name"] if s else "",
            "plate": plate or "",
        }

    # ---------- customers ----------
    def _get(self, phone):
        k = norm_phone(phone)
        if not k:
            raise ApiError(400, "Enter a 10-digit SA cell number, like 082 123 4567.")
        c = self.store.get_customer(k)
        return k, c

    @staticmethod
    def _public(k, c):
        return {
            "phone": k,
            "name": c.get("name", ""),
            "count": int(c.get("count", 0)),
            "total": int(c.get("total", 0)),
            "freeWashes": int(c.get("freeWashes", 0)),
            "wa": bool(c.get("wa", True)),
            "cars": cars_of(c),
            "canUndo": bool(c.get("last")),
            "lastVisit": c.get("lastVisit", 0),
            "createdAt": c.get("createdAt", 0),
        }

    def list_customers(self):
        rows = [self._public(k, c) for k, c in self.store.list_customers()]
        rows.sort(key=lambda r: r["lastVisit"] or 0, reverse=True)
        return {"customers": rows}

    def get_customer(self, key):
        """Look up by cell number or by car registration."""
        k = norm_phone(key)
        if not k:
            plate = norm_plate(key)
            if not plate:
                raise ApiError(400, "Enter a cell number (082 123 4567) or a car registration.")
            k = self.store.get_plate(plate)
            if not k:
                return {"customer": None, "phone": None, "plate": plate}
        c = self.store.get_customer(k)
        if not c:
            return {"customer": None, "phone": k, "plate": None}
        return {"customer": self._public(k, c)}

    def _visit_id(self):
        now = self.clock()
        return sa_day(now), "%013d-%s" % (int(now * 1000), secrets.token_hex(3))

    def _claim_plate(self, plate, k):
        owner = self.store.get_plate(plate)
        if owner and owner != k:
            raise ApiError(409, "Registration %s is already on customer %s." % (plate, "0" + owner[2:]))

    def create_customer(self, body):
        k = norm_phone(body.get("phone"))
        if not k:
            raise ApiError(400, "Enter a 10-digit SA cell number, like 082 123 4567.")
        if body.get("consent") is not True:
            raise ApiError(400, "The customer must agree before we save their number.")
        name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()[:40] or "Customer"
        plate = None
        if body.get("plate"):
            plate = norm_plate(body.get("plate"))
            if not plate:
                raise ApiError(400, "That registration doesn't look right. Use letters and numbers only.")
            self._claim_plate(plate, k)
        now = self.clock()
        c = {"cars": plate or ""}
        v = self._visit_details(body, c)
        day, row = self._visit_id()
        count = min(PAID, v["stamps"])
        c.update({"name": name, "count": count, "total": 1, "freeWashes": 0, "wa": bool(body.get("wa", True)),
                  "consentAt": now, "createdAt": now, "lastVisit": now,
                  "last": "%s|%s|%d|new" % (day, row, count)})
        try:
            self.store.create_customer(k, c)
        except Conflict:
            raise ApiError(409, "This number is already a customer.")
        if plate:
            self.store.set_plate(plate, k)
        self._log(day, row, "new", k, c, v)
        msg = self._message("loyalty_welcome", k, c, card=count)
        return {"customer": self._public(k, c), "message": msg}

    def _mutate(self, phone, fn):
        """Read-modify-write with optimistic concurrency, retried on conflict."""
        for _ in range(4):
            k, c = self._get(phone)
            if not c:
                raise ApiError(404, "Customer not found. They may have been deleted.")
            etag = c.pop("_etag", None)
            new = fn(dict(c))
            try:
                self.store.update_customer(k, new, etag)
                return k, c, new
            except Conflict:
                continue
        raise ApiError(409, "Someone else updated this customer. Try again.")

    def action(self, phone, action, body=None):
        body = body or {}
        now = self.clock()

        if action in ("wash", "free"):
            day, row = self._visit_id()
            info = {}

            def fn(c):
                v = self._visit_details(body, c)
                info["v"] = v
                if action == "wash":
                    if c["count"] >= PAID:
                        raise ApiError(409, "The card is full. Give the Free Wash instead.")
                    new_count = min(PAID, c["count"] + v["stamps"])
                    c["last"] = "%s|%s|%d|wash" % (day, row, new_count - c["count"])
                    c["count"] = new_count
                else:
                    if c["count"] < PAID:
                        raise ApiError(409, "The card isn't full yet.")
                    c["last"] = "%s|%s|%d|free" % (day, row, c["count"])
                    c["count"] = 0
                    c["freeWashes"] = c.get("freeWashes", 0) + 1
                c["total"] = c.get("total", 0) + 1
                c["lastVisit"] = now
                return c
            k, _, c = self._mutate(phone, fn)
            v = info["v"]
            if action == "free":
                v = {**v, "price": 0}
            self._log(day, row, action, k, c, v)
            if action == "free":
                msg = self._message("loyalty_redeemed", k, c, card=0)
            elif c["count"] >= PAID:
                msg = self._message("loyalty_full", k, c, card=PAID)
            else:
                msg = self._message("loyalty_stamp", k, c, card=c["count"],
                                    params=[c["count"], PAID - c["count"]])
        elif action == "undo":
            undone = {}

            def fn(c):
                if not c.get("last"):
                    raise ApiError(409, "There is no wash to undo.")
                day, row, n, kind = c["last"].split("|")
                if kind == "free":
                    c["count"] = int(n)
                    c["freeWashes"] = max(0, c.get("freeWashes", 0) - 1)
                else:
                    c["count"] = max(0, c["count"] - int(n))
                c["total"] = max(0, c.get("total", 0) - 1)
                c["last"] = ""
                undone.update(day=day, row=row)
                return c
            k, _, c = self._mutate(phone, fn)
            try:
                self.store.update_visit(undone["day"], undone["row"], {"void": True})
            except Exception:
                pass
            msg = None
        elif action == "ready":
            k, c = self._get(phone)
            if not c:
                raise ApiError(404, "Customer not found.")
            c.pop("_etag", None)
            msg = self._message("car_ready", k, c)
        else:
            raise ApiError(404, "Unknown action.")
        return {"customer": self._public(k, c), "message": msg}

    def set_whatsapp(self, phone, wa):
        def fn(c):
            c["wa"] = bool(wa)
            return c
        k, _, c = self._mutate(phone, fn)
        return {"customer": self._public(k, c)}

    def add_car(self, phone, raw):
        plate = norm_plate(raw)
        if not plate:
            raise ApiError(400, "That registration doesn't look right. Use letters and numbers only.")
        k = norm_phone(phone)
        self._claim_plate(plate, k)

        def fn(c):
            cars = cars_of(c)
            if plate not in cars:
                if len(cars) >= 5:
                    raise ApiError(400, "A customer can have up to 5 cars.")
                cars.append(plate)
            c["cars"] = ",".join(cars)
            return c
        k, _, c = self._mutate(phone, fn)
        self.store.set_plate(plate, k)
        return {"customer": self._public(k, c)}

    def remove_car(self, phone, raw):
        plate = norm_plate(raw)

        def fn(c):
            c["cars"] = ",".join(p for p in cars_of(c) if p != plate)
            return c
        k, _, c = self._mutate(phone, fn)
        if plate and self.store.get_plate(plate) == k:
            self.store.delete_plate(plate)
        return {"customer": self._public(k, c)}

    def delete_customer(self, phone):
        k, c = self._get(phone)
        if c:
            for p in cars_of(c):
                if self.store.get_plate(p) == k:
                    self.store.delete_plate(p)
            self.store.delete_customer(k)
        return {"ok": True}

    # ---------- today ----------
    def _log(self, day, row, kind, k, c, v):
        try:
            self.store.add_visit(day, row, {
                "type": kind, "phone": k, "name": c.get("name", ""), "count": int(c.get("count", 0)),
                "at": self.clock(), "washType": v["washType"], "price": v["price"], "stamps": v["stamps"],
                "staff": v["staff"], "plate": v["plate"], "void": False})
        except Exception:
            pass   # the log is a convenience; never fail a wash because of it

    def today(self):
        rows = self.store.list_visits(sa_day(self.clock()))
        rows.sort(key=lambda r: r["at"], reverse=True)
        live = [r for r in rows if not r.get("void")]
        paid = [r for r in live if r["type"] in ("wash", "new")]
        by_staff, by_type = {}, {}
        for r in live:
            if r["type"] in ("wash", "new", "free"):
                if r.get("staff"):
                    by_staff[r["staff"]] = by_staff.get(r["staff"], 0) + 1
                if r.get("washType"):
                    by_type[r["washType"]] = by_type.get(r["washType"], 0) + 1
        return {
            "paid": len(paid),
            "takings": round(sum(float(r.get("price") or 0) for r in paid), 2),
            "free": sum(1 for r in live if r["type"] == "free"),
            "new": sum(1 for r in live if r["type"] == "new"),
            "byStaff": sorted(by_staff.items(), key=lambda x: -x[1]),
            "byType": sorted(by_type.items(), key=lambda x: -x[1]),
            "log": rows[:150],
        }

    # ---------- WhatsApp ----------
    def _message(self, template, k, c, card=None, params=None):
        first = c.get("name") or ""
        first = "there" if first in ("", "Customer") else first
        params = [first] + list(params or [])
        text = render(template, params)
        image = None
        if TEMPLATES[template]["image"] and card is not None and self.cfg["public_url"]:
            image = "%s/cards/card-%d.png" % (self.cfg["public_url"], card)
        # The "Open in WhatsApp" message can't attach a picture, but WhatsApp shows the card page's
        # og:image as a large preview, so the customer still sees their stamp card.
        card_link = "%s/c/%d/" % (self.cfg["public_url"], card) if card is not None and self.cfg["public_url"] else ""
        manual = (text + ("\n\nYour loyalty card: " + card_link if card_link else "")
                  + ("\n\nRate our service: " + self.cfg["review_url"] if self.cfg["review_url"] else ""))
        out = {"template": template, "text": text if self.sender is not None else manual,
               "image": image, "card": card, "cardLink": card_link,
               "waLink": "https://wa.me/%s?text=%s" % (k, urllib.parse.quote(manual)),
               "auto": False, "sent": False, "error": None}
        if not c.get("wa", True):
            out["error"] = "This customer turned off WhatsApp updates."
            out["waLink"] = None
            return out
        if self.sender is not None:
            out["auto"] = True
            try:
                self.sender.send_template(k, template, params, image)
                out["sent"] = True
            except Exception as e:  # fall back to the manual link
                out["error"] = "Automatic sending failed (%s). Send it yourself with the button." % str(e)[:120]
        return out

    # ---------- WhatsApp replies (webhook) ----------
    def verify_webhook(self, params):
        """Meta's one-time check when the webhook address is saved."""
        token = self.cfg.get("wa_verify_token") or ""
        if (params.get("hub.mode") == "subscribe" and token
                and hmac.compare_digest(str(params.get("hub.verify_token") or ""), token)):
            return str(params.get("hub.challenge") or "")
        raise ApiError(403, "Verification failed.")

    def handle_webhook(self, raw, signature):
        """Store customer replies, honour STOP/START, and send one automatic answer."""
        secret = self.cfg.get("wa_app_secret") or ""
        good = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not secret or not hmac.compare_digest(str(signature or ""), good):
            raise ApiError(401, "Bad signature.")
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            raise ApiError(400, "Invalid body.")
        handled = 0
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "")
                         for c in value.get("contacts") or []}
                for m in value.get("messages") or []:
                    self._incoming(m, names.get(m.get("from"), ""))
                    handled += 1
        return {"ok": True, "handled": handled}

    @staticmethod
    def _message_text(m):
        kind = m.get("type")
        if kind == "text":
            return (m.get("text") or {}).get("body", "")
        if kind == "button":
            return (m.get("button") or {}).get("text", "")
        if kind == "interactive":
            i = m.get("interactive") or {}
            return ((i.get("button_reply") or i.get("list_reply") or {}).get("title", ""))
        return "[%s]" % (kind or "message")

    def _incoming(self, m, profile_name):
        msg_id = str(m.get("id") or "")
        sender = re.sub(r"\D", "", str(m.get("from") or ""))
        if not msg_id or not sender:
            return
        key = hashlib.sha1(msg_id.encode()).hexdigest()
        if self.store.get_reply(key):
            return                                   # Meta retries deliveries; handle each message once
        text = self._message_text(m)[:1000]
        word = text.strip().lower().strip(".! ")
        now = self.clock()
        c = self.store.get_customer(sender)
        kind = "message"
        if word in ("stop", "unsubscribe", "opt out", "optout", "stop all"):
            kind = "stop"
        elif word in ("start", "subscribe", "unstop"):
            kind = "start"
        if c and kind in ("stop", "start"):
            try:
                self.set_whatsapp(sender, kind == "start")
            except ApiError:
                pass
        self.store.add_reply(key, {"phone": sender, "name": (c or {}).get("name") or profile_name or "",
                                   "customer": bool(c), "text": text, "kind": kind,
                                   "at": float(m.get("timestamp") or now)})
        if self.sender is None:
            return
        b, chat = self.cfg["business"], self.cfg["chat_number"]
        if kind == "stop":
            answer = ("You won't get %s loyalty messages anymore. Your stamps are still saved. "
                      "Reply START to turn them back on." % b)
        elif kind == "start":
            answer = "Welcome back! You'll get your %s loyalty card updates again. 🚗✨" % b
        else:
            last = (self.store.get_setting("autoreply-" + sender) or {}).get("at", 0)
            if now - last < 12 * 3600:
                return                               # answer each person at most once every 12 hours
            self.store.set_setting("autoreply-" + sender, {"at": now})
            answer = ("Thanks for your message! This number only sends %s loyalty updates. "
                      "To chat with us, WhatsApp %s." % (b, chat))
        try:
            self.sender.send_text(sender, answer)
        except Exception:
            pass                                     # never make Meta retry because our answer failed

    def replies(self):
        rows = self.store.list_replies()
        rows.sort(key=lambda r: r["at"], reverse=True)
        seen = (self.store.get_setting("replies-seen") or {}).get("at", 0)
        for r in rows:
            r["unread"] = r["at"] > seen
        return {"unread": sum(1 for r in rows if r["unread"]), "replies": rows[:200]}

    def mark_replies_seen(self):
        self.store.set_setting("replies-seen", {"at": self.clock()})
        return {"ok": True}
