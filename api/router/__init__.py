"""One HTTP function that routes every /api/* call."""
import json
import logging
import os
import re

import azure.functions as func

from core import ApiError, Rewards
from store import TableStore
from whatsapp import WhatsAppSender

_svc = None


def service():
    global _svc
    if _svc is None:
        _svc = Rewards.from_env(TableStore(os.environ["STORAGE_CONNECTION"]), WhatsAppSender.from_env())
    return _svc


def client_ip(req):
    for h in ("x-azure-clientip", "x-client-ip", "x-forwarded-for"):
        v = req.headers.get(h)
        if v:
            ip = v.split(",")[0].strip()
            return ip.rsplit(":", 1)[0] if ip.count(":") == 1 else ip   # drop the port from "1.2.3.4:5678"
    return "unknown"


def dispatch(svc, method, path, body, token, ip):
    if path == "login" and method == "POST":
        return svc.login(body.get("pin"), ip)

    role = svc.require(token)       # everything below needs a valid PIN session

    if path == "config" and method == "GET":
        return svc.config(role)
    if path == "today" and method == "GET":
        return svc.today()
    if path == "customers":
        if method == "GET":
            return svc.list_customers()
        if method == "POST":
            return svc.create_customer(body)
    m = re.fullmatch(r"customers/([^/]+)", path)
    if m:
        if method == "GET":
            return svc.get_customer(m.group(1))
        if method == "PATCH":
            return svc.set_whatsapp(m.group(1), body.get("wa"))
        if method == "DELETE":
            return svc.delete_customer(m.group(1))
    m = re.fullmatch(r"customers/([^/]+)/(wash|free|undo|ready)", path)
    if m and method == "POST":
        return svc.action(m.group(1), m.group(2), body)
    m = re.fullmatch(r"customers/([^/]+)/cars", path)
    if m and method == "POST":
        return svc.add_car(m.group(1), body.get("plate"))
    m = re.fullmatch(r"customers/([^/]+)/cars/([^/]+)", path)
    if m and method == "DELETE":
        return svc.remove_car(m.group(1), m.group(2))
    if path == "replies" and method == "GET":
        return svc.replies()
    if path == "replies/seen" and method == "POST":
        return svc.mark_replies_seen()
    m = re.fullmatch(r"reports/(week|month)/(\d{4}-\d{2}-\d{2})", path)
    if m and method == "GET":
        svc.require(token, "admin")
        return svc.report(m.group(1), m.group(2))
    if path == "settings/hours" and method == "POST":
        svc.require(token, "admin")
        return svc.save_hours(body)
    if path == "settings/catalog" and method == "POST":
        svc.require(token, "admin")
        return svc.save_catalog(body)
    if path == "settings/staff-pin" and method == "POST":
        svc.require(token, "admin")
        return svc.change_staff_pin(body.get("pin"))
    raise ApiError(404, "Not found.")


def main(req: func.HttpRequest) -> func.HttpResponse:
    path = (req.route_params.get("path") or "").strip("/")
    try:
        if path == "whatsapp":      # Meta's webhook: no PIN; checked by verify token / signature instead
            if req.method.upper() == "GET":
                return func.HttpResponse(service().verify_webhook(dict(req.params)), mimetype="text/plain")
            result = service().handle_webhook(req.get_body(), req.headers.get("x-hub-signature-256"))
            return func.HttpResponse(json.dumps(result), mimetype="application/json")
        try:
            body = req.get_json() if req.get_body() else {}
        except ValueError:
            raise ApiError(400, "Invalid request.")
        if not isinstance(body, dict):
            raise ApiError(400, "Invalid request.")
        result = dispatch(service(), req.method.upper(), path, body, req.headers.get("x-session"), client_ip(req))
        return func.HttpResponse(json.dumps(result), mimetype="application/json",
                                 headers={"Cache-Control": "no-store"})
    except ApiError as e:
        return func.HttpResponse(json.dumps({"error": e.message}), status_code=e.status,
                                 mimetype="application/json")
    except Exception as e:
        logging.exception("Unhandled error on %s %s", req.method, path)
        return func.HttpResponse(json.dumps({"error": "Something went wrong on the server (%s). Try again." % type(e).__name__}),
                                 status_code=500, mimetype="application/json")
