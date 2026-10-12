"""Send WhatsApp template messages through Meta's Cloud API or 360dialog.

WA_PROVIDER = off | meta | 360dialog
  meta:      WA_TOKEN (permanent access token), WA_PHONE_NUMBER_ID, optional WA_API_VERSION
  360dialog: WA_TOKEN (D360 API key)
Both use the same Cloud API message body; only the URL and auth header differ.
"""
import json
import os
import urllib.request
import urllib.error


class WhatsAppSender:
    def __init__(self, provider, token, phone_number_id="", api_version="v23.0", language="en"):
        self.language = language
        if provider == "meta":
            if not (token and phone_number_id):
                raise RuntimeError("WA_TOKEN and WA_PHONE_NUMBER_ID are required for WA_PROVIDER=meta")
            self.url = "https://graph.facebook.com/%s/%s/messages" % (api_version, phone_number_id)
            self.headers = {"Authorization": "Bearer " + token}
        elif provider == "360dialog":
            if not token:
                raise RuntimeError("WA_TOKEN is required for WA_PROVIDER=360dialog")
            self.url = "https://waba-v2.360dialog.io/messages"
            self.headers = {"D360-API-KEY": token}
        else:
            raise RuntimeError("Unknown WA_PROVIDER: %s" % provider)

    @classmethod
    def from_env(cls):
        provider = os.environ.get("WA_PROVIDER", "off").strip().lower()
        if provider in ("", "off"):
            return None
        return cls(provider, os.environ.get("WA_TOKEN", ""), os.environ.get("WA_PHONE_NUMBER_ID", ""),
                   os.environ.get("WA_API_VERSION", "v23.0"), os.environ.get("WA_LANGUAGE", "en"))

    def build(self, to, template, params, image_url):
        components = []
        if image_url:
            components.append({"type": "header", "parameters": [{"type": "image", "image": {"link": image_url}}]})
        components.append({"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in params]})
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {"name": template, "language": {"code": self.language}, "components": components},
        }

    def send_text(self, to, text):
        """Free-form text. Only allowed within 24 hours of the customer's last message to us."""
        return self._post({"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                           "type": "text", "text": {"body": text, "preview_url": False}})

    def send_template(self, to, template, params, image_url=None):
        return self._post(self.build(to, template, params, image_url))

    def _post(self, payload):
        body = json.dumps(payload).encode()
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={**self.headers, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            try:
                err = json.loads(detail).get("error", {})
                detail = err.get("error_data", {}).get("details") or err.get("message") or detail
            except Exception:
                pass
            raise RuntimeError("WhatsApp %s: %s" % (e.code, detail[:200]))
