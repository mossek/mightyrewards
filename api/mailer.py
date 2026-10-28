"""Send email through Azure Communication Services (no extra packages needed).

ACS_CONNECTION = the Communication Service connection string ("endpoint=https://...;accesskey=...")
ACS_SENDER     = the sender address, e.g. DoNotReply@<id>.azurecomm.net
"""
import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from email.utils import formatdate
from urllib.parse import urlparse

API = "/emails:send?api-version=2023-03-31"


class AcsMailer:
    def __init__(self, conn_str, sender):
        parts = dict(p.split("=", 1) for p in conn_str.split(";") if "=" in p)
        self.endpoint = parts["endpoint"].rstrip("/")
        self.key = base64.b64decode(parts["accesskey"])
        self.sender = sender

    @classmethod
    def from_env(cls):
        conn, sender = os.environ.get("ACS_CONNECTION", ""), os.environ.get("ACS_SENDER", "")
        return cls(conn, sender) if conn and sender else None

    def headers(self, body, date=None):
        """Azure's HMAC request signing (documented 'HMAC-SHA256' scheme)."""
        date = date or formatdate(usegmt=True)
        content_hash = base64.b64encode(hashlib.sha256(body).digest()).decode()
        host = urlparse(self.endpoint).netloc
        to_sign = "POST\n%s\n%s;%s;%s" % (API, date, host, content_hash)
        sig = base64.b64encode(hmac.new(self.key, to_sign.encode(), hashlib.sha256).digest()).decode()
        return {
            "Content-Type": "application/json",
            "x-ms-date": date,
            "x-ms-content-sha256": content_hash,
            "Authorization": "HMAC-SHA256 SignedHeaders=x-ms-date;host;x-ms-content-sha256&Signature=" + sig,
        }

    def send(self, to, subject, text, html):
        body = json.dumps({
            "senderAddress": self.sender,
            "recipients": {"to": [{"address": a} for a in to]},
            "content": {"subject": subject, "plainText": text, "html": html},
        }).encode()
        req = urllib.request.Request(self.endpoint + API, data=body, method="POST", headers=self.headers(body))
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise RuntimeError("Email %s: %s" % (e.code, detail))
