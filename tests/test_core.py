import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
try:
    import azure.functions  # noqa: F401
except ImportError:          # router imports it; a stub is enough to test dispatch()
    _fn = types.ModuleType("azure.functions")
    _fn.HttpRequest = _fn.HttpResponse = object
    _az = sys.modules.setdefault("azure", types.ModuleType("azure"))
    _az.functions = _fn
    sys.modules["azure.functions"] = _fn

from core import ApiError, Rewards, norm_phone, PAID      # noqa: E402
from store import MemoryStore                             # noqa: E402
from whatsapp import WhatsAppSender                       # noqa: E402
from router import dispatch                               # noqa: E402

CFG = {"token_secret": "x" * 40, "admin_pin": "908172", "staff_pin": "1234", "business": "Mighty Car-Wash",
       "location": "Bolobedu Matipane Molai", "public_url": "https://example.test", "review_url": "https://g.page/r/abc",
       "chat_number": "078 667 7610", "wa_verify_token": "verify-me", "wa_app_secret": "app-secret"}


class Clock:
    def __init__(self):
        self.t = 1_790_000_000.0

    def __call__(self):
        return self.t


class FakeSender:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_template(self, to, template, params, image):
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append((to, template, params, image))

    def send_text(self, to, text):
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append((to, "text", text, None))


def make(sender=None):
    clock = Clock()
    return Rewards(MemoryStore(), sender, dict(CFG), clock), clock


class PhoneTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(norm_phone("078 667 7610"), "27786677610")
        self.assertEqual(norm_phone("+27 78 667 7610"), "27786677610")
        self.assertIsNone(norm_phone("12345"))
        self.assertIsNone(norm_phone("011 123 4567"))   # landline


class LoginTests(unittest.TestCase):
    def test_staff_and_admin(self):
        svc, _ = make()
        staff = svc.login("1234", "1.1.1.1")
        self.assertEqual(staff["role"], "staff")
        self.assertEqual(svc.require(staff["token"]), "staff")
        with self.assertRaises(ApiError) as e:
            svc.require(staff["token"], "admin")
        self.assertEqual(e.exception.status, 403)
        admin = svc.login("908172", "1.1.1.1")
        self.assertEqual(svc.require(admin["token"], "admin"), "admin")

    def test_lockout_after_five_wrong(self):
        svc, clock = make()
        for _ in range(4):
            with self.assertRaises(ApiError) as e:
                svc.login("0000", "2.2.2.2")
            self.assertEqual(e.exception.status, 401)
        with self.assertRaises(ApiError) as e:
            svc.login("0000", "2.2.2.2")
        self.assertEqual(e.exception.status, 429)
        with self.assertRaises(ApiError):
            svc.login("1234", "2.2.2.2")            # even the right PIN waits
        svc.login("1234", "3.3.3.3")                # other phones unaffected
        clock.t += 16 * 60
        svc.login("1234", "2.2.2.2")

    def test_pin_change_ends_staff_sessions(self):
        svc, _ = make()
        old = svc.login("1234", "1.1.1.1")["token"]
        svc.change_staff_pin("5678")
        with self.assertRaises(ApiError):
            svc.require(old)
        with self.assertRaises(ApiError):
            svc.login("1234", "4.4.4.4")
        self.assertEqual(svc.login("5678", "1.1.1.1")["role"], "staff")

    def test_expiry_and_tamper(self):
        svc, clock = make()
        tok = svc.login("1234", "1.1.1.1")["token"]
        with self.assertRaises(ApiError):
            svc.require(tok[:-2] + "aa")
        clock.t += 13 * 3600
        with self.assertRaises(ApiError):
            svc.require(tok)


class CardTests(unittest.TestCase):
    def test_full_cycle_manual(self):
        svc, _ = make()
        with self.assertRaises(ApiError):
            svc.create_customer({"phone": "0786677610", "name": "Moshe"})     # no consent
        r = svc.create_customer({"phone": "0786677610", "name": "Moshe", "consent": True, "wa": True})
        self.assertEqual(r["customer"]["count"], 1)
        self.assertEqual(r["message"]["template"], "loyalty_welcome")
        self.assertFalse(r["message"]["auto"])
        self.assertIn("wa.me/27786677610", r["message"]["waLink"])
        self.assertIn("Rate+our+service".replace("+", "%20"), r["message"]["waLink"])
        for n in range(2, PAID + 1):
            r = svc.action("078 667 7610", "wash")
            self.assertEqual(r["customer"]["count"], n)
        self.assertEqual(r["message"]["template"], "loyalty_full")
        self.assertEqual(r["message"]["image"], "https://example.test/cards/card-9.png")
        with self.assertRaises(ApiError):
            svc.action("0786677610", "wash")          # full card: must give free wash
        r = svc.action("0786677610", "free")
        self.assertEqual((r["customer"]["count"], r["customer"]["total"], r["customer"]["freeWashes"]), (0, 10, 1))
        self.assertEqual(r["message"]["template"], "loyalty_redeemed")
        t = svc.today()
        self.assertEqual((t["paid"], t["free"], t["new"]), (9, 1, 1))

    def test_stamp_message_text(self):
        svc, _ = make()
        svc.create_customer({"phone": "0821234567", "name": "Thabo", "consent": True})
        r = svc.action("0821234567", "wash")
        self.assertIn("You've done 2 of 9 washes", r["message"]["text"])
        self.assertIn("Only 7 more", r["message"]["text"])
        self.assertNotIn("{{", r["message"]["text"])
        self.assertEqual(r["message"]["image"], "https://example.test/cards/card-2.png")
        # free method: the message carries the card page link so WhatsApp shows the card picture
        self.assertIn("Your loyalty card: https://example.test/c/2/", r["message"]["text"])
        self.assertIn("https%3A//example.test/c/2/", r["message"]["waLink"])
        self.assertNotIn("%F0%9F", r["message"]["waLink"])       # no emojis: WhatsApp Desktop shows them as ??
        self.assertIn("Hi Thabo\n\nYour Mighty", r["message"]["text"])
        r = svc.action("0821234567", "ready")
        self.assertNotIn("/c/", r["message"]["text"])            # "car ready" has no card
        self.assertIn("at Mighty Car-Wash.\n\nRate our service", r["message"]["text"])

    def test_auto_send_and_fallback(self):
        sender = FakeSender()
        svc, _ = make(sender)
        r = svc.create_customer({"phone": "0821234567", "name": "", "consent": True})
        self.assertTrue(r["message"]["sent"])
        self.assertEqual(sender.sent[0][:3], ("27821234567", "loyalty_welcome", ["there"]))
        svc.sender = FakeSender(fail=True)
        r = svc.action("0821234567", "ready")
        self.assertTrue(r["message"]["auto"])
        self.assertFalse(r["message"]["sent"])
        self.assertIn("wa.me", r["message"]["waLink"])

    def test_opted_out_gets_nothing(self):
        sender = FakeSender()
        svc, _ = make(sender)
        svc.create_customer({"phone": "0821234567", "name": "A", "consent": True, "wa": False})
        r = svc.action("0821234567", "wash")
        self.assertFalse(r["message"]["sent"])
        self.assertIsNone(r["message"]["waLink"])
        self.assertEqual(sender.sent, [])

    def test_undo_delete_and_conflict_retry(self):
        svc, _ = make()
        svc.create_customer({"phone": "0821234567", "name": "A", "consent": True})
        self.assertEqual(svc.action("0821234567", "undo")["customer"]["count"], 0)
        with self.assertRaises(ApiError):
            svc.action("0821234567", "undo")
        # simulate another phone writing between our read and write
        real = svc.store.update_customer
        calls = {"n": 0}

        def racy(k, data, etag):
            calls["n"] += 1
            if calls["n"] == 1:
                cur = svc.store.get_customer(k)
                real(k, {**{x: y for x, y in cur.items() if x != "_etag"}, "count": 5}, cur["_etag"])
            return real(k, data, etag)
        svc.store.update_customer = racy
        self.assertEqual(svc.action("0821234567", "wash")["customer"]["count"], 6)
        svc.store.update_customer = real
        svc.delete_customer("0821234567")
        self.assertIsNone(svc.get_customer("0821234567")["customer"])


class VisitDetailTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.clock = make()
        cat = self.svc.save_catalog({
            "types": [{"name": "Full wash", "price": "120"}, {"name": "Valet", "price": 350, "stamps": 2}],
            "staff": [{"name": "Sipho"}, {"name": "Lerato"}]})
        self.full, self.valet = cat["types"]
        self.sipho, self.lerato = cat["staff"]

    def visit(self, t, s, **extra):
        self.clock.t += 60          # each visit a minute later, so the log order is defined
        return {"type": t["id"], "staff": s["id"], **extra}

    def test_type_and_staff_required_once_set_up(self):
        with self.assertRaises(ApiError) as e:
            self.svc.create_customer({"phone": "0821234567", "name": "A", "consent": True})
        self.assertIn("wash type", e.exception.message)
        with self.assertRaises(ApiError) as e:
            self.svc.create_customer({"phone": "0821234567", "name": "A", "consent": True, "type": self.full["id"]})
        self.assertIn("who washed", e.exception.message)

    def test_takings_staff_and_types(self):
        self.svc.create_customer({"phone": "0821234567", "name": "A", "consent": True, **self.visit(self.full, self.sipho)})
        r = self.svc.action("0821234567", "wash", self.visit(self.valet, self.lerato))
        self.assertEqual(r["customer"]["count"], 3)              # valet gives 2 stamps
        t = self.svc.today()
        self.assertEqual((t["paid"], t["takings"]), (2, 470.0))
        self.assertEqual(dict(t["byStaff"]), {"Sipho": 1, "Lerato": 1})
        self.assertEqual(dict(t["byType"]), {"Full wash": 1, "Valet": 1})
        self.assertEqual(t["log"][0]["staff"], "Lerato")

    def test_valet_caps_at_full_card_and_undo_reverses_exactly(self):
        self.svc.create_customer({"phone": "0821234567", "name": "A", "consent": True, **self.visit(self.full, self.sipho)})
        for _ in range(7):
            self.svc.action("0821234567", "wash", self.visit(self.full, self.sipho))
        r = self.svc.action("0821234567", "wash", self.visit(self.valet, self.sipho))   # 8 + 2 -> capped at 9
        self.assertEqual(r["customer"]["count"], PAID)
        r = self.svc.action("0821234567", "undo")
        self.assertEqual(r["customer"]["count"], 8)                 # only the 1 stamp actually added comes off
        self.assertFalse(r["customer"]["canUndo"])
        t = self.svc.today()
        self.assertEqual((t["paid"], t["takings"]), (8, 960.0))     # undone valet not counted
        self.assertTrue(any(x.get("void") for x in t["log"]))

    def test_free_wash_and_its_undo(self):
        self.svc.create_customer({"phone": "0821234567", "name": "A", "consent": True, **self.visit(self.full, self.sipho)})
        for _ in range(8):
            self.svc.action("0821234567", "wash", self.visit(self.full, self.sipho))
        r = self.svc.action("0821234567", "free", self.visit(self.valet, self.lerato))
        self.assertEqual((r["customer"]["count"], r["customer"]["freeWashes"]), (0, 1))
        self.assertEqual(self.svc.today()["takings"], 1080.0)      # free wash adds R0
        r = self.svc.action("0821234567", "undo")
        self.assertEqual((r["customer"]["count"], r["customer"]["freeWashes"]), (PAID, 0))

    def test_registration_lookup_and_ownership(self):
        r = self.svc.create_customer({"phone": "0821234567", "name": "A", "consent": True, "plate": "ca 123-456",
                                      **self.visit(self.full, self.sipho)})
        self.assertEqual(r["customer"]["cars"], ["CA123456"])
        self.assertEqual(self.svc.get_customer("CA 123 456")["customer"]["phone"], "27821234567")
        self.assertIsNone(self.svc.get_customer("XYZ 999 GP")["customer"])
        with self.assertRaises(ApiError) as e:
            self.svc.create_customer({"phone": "0831112222", "name": "B", "consent": True, "plate": "CA123456",
                                      **self.visit(self.full, self.sipho)})
        self.assertEqual(e.exception.status, 409)
        self.svc.add_car("0821234567", "BX 12 YZ GP")
        r = self.svc.action("0821234567", "wash", self.visit(self.full, self.sipho, plate="BX12YZGP"))
        self.assertEqual(self.svc.today()["log"][0]["plate"], "BX12YZGP")
        with self.assertRaises(ApiError):
            self.svc.action("0821234567", "wash", self.visit(self.full, self.sipho, plate="ND999"))
        self.svc.remove_car("0821234567", "CA123456")
        self.assertIsNone(self.svc.get_customer("CA123456")["customer"])
        self.svc.delete_customer("0821234567")
        self.assertIsNone(self.svc.get_customer("BX12YZGP")["customer"])

    def test_catalog_validation(self):
        with self.assertRaises(ApiError):
            self.svc.save_catalog({"types": [{"name": "X", "price": "abc"}], "staff": []})
        with self.assertRaises(ApiError):
            self.svc.save_catalog({"types": [{"name": "X", "price": 10, "stamps": 5}], "staff": []})
        with self.assertRaises(ApiError):
            self.svc.save_catalog({"types": [], "staff": [{"name": ""}]})
        cat = self.svc.save_catalog({"types": [{"id": self.full["id"], "name": "Full wash", "price": 130}], "staff": []})
        self.assertEqual(cat["types"][0]["id"], self.full["id"])    # ids kept when editing


def webhook(svc, sender, text, msg_id, secret="app-secret"):
    import hashlib, hmac, json
    raw = json.dumps({"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": sender, "profile": {"name": "WA Name"}}],
        "messages": [{"from": sender, "id": msg_id, "timestamp": str(int(svc.clock())), "type": "text",
                      "text": {"body": text}}]}}]}]}).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return svc.handle_webhook(raw, sig)


class ReplyTests(unittest.TestCase):
    def setUp(self):
        self.sender = FakeSender()
        self.svc, self.clock = make(self.sender)
        self.svc.create_customer({"phone": "0821234567", "name": "Thabo", "consent": True})
        self.sender.sent.clear()

    def test_verify(self):
        ok = {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "123"}
        self.assertEqual(self.svc.verify_webhook(ok), "123")
        with self.assertRaises(ApiError):
            self.svc.verify_webhook({**ok, "hub.verify_token": "wrong"})

    def test_bad_signature_rejected(self):
        with self.assertRaises(ApiError) as e:
            webhook(self.svc, "27821234567", "hi", "wamid.1", secret="not-the-secret")
        self.assertEqual(e.exception.status, 401)
        self.assertEqual(self.svc.replies()["replies"], [])

    def test_message_stored_answered_once_and_deduped(self):
        webhook(self.svc, "27821234567", "Are you open Sunday?", "wamid.1")
        webhook(self.svc, "27821234567", "Are you open Sunday?", "wamid.1")      # Meta retry
        webhook(self.svc, "27821234567", "Hello?", "wamid.2")                    # within 12 hours
        r = self.svc.replies()
        self.assertEqual(r["unread"], 2)
        self.assertEqual(r["replies"][0]["name"], "Thabo")
        answers = [x for x in self.sender.sent if x[1] == "text"]
        self.assertEqual(len(answers), 1)
        self.assertIn("078 667 7610", answers[0][2])
        self.svc.mark_replies_seen()
        self.assertEqual(self.svc.replies()["unread"], 0)
        self.clock.t += 13 * 3600
        webhook(self.svc, "27821234567", "Hi again", "wamid.3")
        self.assertEqual(len([x for x in self.sender.sent if x[1] == "text"]), 2)

    def test_stop_and_start(self):
        webhook(self.svc, "27821234567", " Stop ", "wamid.1")
        self.assertFalse(self.svc.get_customer("0821234567")["customer"]["wa"])
        self.assertIn("START", self.sender.sent[-1][2])
        r = self.svc.action("0821234567", "wash")
        self.assertIsNone(r["message"]["waLink"])                                 # nothing sent after STOP
        webhook(self.svc, "27821234567", "start", "wamid.2")
        self.assertTrue(self.svc.get_customer("0821234567")["customer"]["wa"])
        self.assertEqual([x["kind"] for x in self.svc.replies()["replies"]][:2].count("stop"), 1)

    def test_unknown_sender_uses_profile_name(self):
        webhook(self.svc, "27831112222", "Who is this?", "wamid.9")
        r = self.svc.replies()["replies"][0]
        self.assertEqual((r["name"], r["customer"]), ("WA Name", False))


class RouterTests(unittest.TestCase):
    def test_routes(self):
        svc, _ = make()
        with self.assertRaises(ApiError) as e:
            dispatch(svc, "GET", "customers", {}, None, "1.1.1.1")
        self.assertEqual(e.exception.status, 401)
        tok = dispatch(svc, "POST", "login", {"pin": "1234"}, None, "1.1.1.1")["token"]
        dispatch(svc, "POST", "customers", {"phone": "0821234567", "name": "A", "consent": True}, tok, "x")
        r = dispatch(svc, "POST", "customers/0821234567/wash", {}, tok, "x")
        self.assertEqual(r["customer"]["count"], 2)
        self.assertEqual(len(dispatch(svc, "GET", "customers", {}, tok, "x")["customers"]), 1)
        with self.assertRaises(ApiError) as e:
            dispatch(svc, "POST", "settings/staff-pin", {"pin": "9999"}, tok, "x")
        self.assertEqual(e.exception.status, 403)


class WhatsAppBodyTests(unittest.TestCase):
    def test_template_body(self):
        s = WhatsAppSender("meta", "tok", "123")
        b = s.build("27821234567", "loyalty_stamp", ["Thabo", 3, 6], "https://x/cards/card-3.png")
        self.assertEqual(b["template"]["components"][0]["parameters"][0]["image"]["link"], "https://x/cards/card-3.png")
        self.assertEqual([p["text"] for p in b["template"]["components"][1]["parameters"]], ["Thabo", "3", "6"])
        self.assertEqual(s.url, "https://graph.facebook.com/v23.0/123/messages")


if __name__ == "__main__":
    unittest.main()
