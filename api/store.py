"""Storage: Azure Table Storage in production, an in-memory dict for tests."""
import json
import re
import uuid

from core import Conflict

DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
COPIED_TABLES = ("customers", "visits", "plates")   # what "copy live data" brings into a test copy
COPIED_SETTINGS = ("catalog", "hours")          # wash types/prices/staff, business hours (never PINs)


class TableStore:
    def __init__(self, conn_str, prefix=""):
        """prefix "" = live data; "test" = the separate tables used by test copies of the app."""
        from azure.data.tables import TableServiceClient
        svc = TableServiceClient.from_connection_string(conn_str)
        self.prefix = prefix
        table = lambda name: svc.create_table_if_not_exists(prefix + name)
        self.customers = table("customers")
        self.visits = table("visits")
        self.settings = table("settings")
        self.plates = table("plates")       # registration -> cell number
        self.replies = table("replies")     # customers' WhatsApp replies

    @staticmethod
    def _clean(entity):
        return {k: v for k, v in entity.items() if k not in ("PartitionKey", "RowKey")}

    def get_customer(self, phone):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            e = self.customers.get_entity("c", phone)
        except ResourceNotFoundError:
            return None
        c = self._clean(e)
        c["_etag"] = e.metadata["etag"]
        return c

    def create_customer(self, phone, data):
        from azure.core.exceptions import ResourceExistsError
        try:
            self.customers.create_entity({"PartitionKey": "c", "RowKey": phone, **data})
        except ResourceExistsError:
            raise Conflict()

    def update_customer(self, phone, data, etag):
        from azure.core import MatchConditions
        from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
        from azure.data.tables import UpdateMode
        try:
            self.customers.update_entity({"PartitionKey": "c", "RowKey": phone, **data},
                                         mode=UpdateMode.REPLACE, etag=etag,
                                         match_condition=MatchConditions.IfNotModified)
        except ResourceNotFoundError:
            raise Conflict()
        except HttpResponseError as e:
            if e.status_code == 412:
                raise Conflict()
            raise

    def delete_customer(self, phone):
        self.customers.delete_entity("c", phone)

    def list_customers(self):
        return [(e["RowKey"], self._clean(e)) for e in self.customers.query_entities("PartitionKey eq 'c'")]

    def add_visit(self, day, row, data):
        self.visits.create_entity({"PartitionKey": day, "RowKey": row, **data})

    def update_visit(self, day, row, patch):
        from azure.data.tables import UpdateMode
        self.visits.update_entity({"PartitionKey": day, "RowKey": row, **patch}, mode=UpdateMode.MERGE)

    def list_visits(self, day):
        if not DAY_RE.fullmatch(day):
            return []
        return [self._clean(e) for e in self.visits.query_entities("PartitionKey eq '%s'" % day)]

    def list_visits_range(self, first_day, last_day):
        """All visits from first_day to last_day inclusive (days are 'YYYY-MM-DD' partitions)."""
        if not (DAY_RE.fullmatch(first_day) and DAY_RE.fullmatch(last_day)):
            return []
        q = "PartitionKey ge '%s' and PartitionKey le '%s'" % (first_day, last_day)
        return [{**self._clean(e), "day": e["PartitionKey"]} for e in self.visits.query_entities(q)]

    def get_plate(self, plate):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return self.plates.get_entity("p", plate)["phone"]
        except ResourceNotFoundError:
            return None

    def set_plate(self, plate, phone):
        self.plates.upsert_entity({"PartitionKey": "p", "RowKey": plate, "phone": phone})

    def delete_plate(self, plate):
        self.plates.delete_entity("p", plate)

    def get_reply(self, key):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return self._clean(self.replies.get_entity("r", key))
        except ResourceNotFoundError:
            return None

    def add_reply(self, key, data):
        self.replies.upsert_entity({"PartitionKey": "r", "RowKey": key, **data})

    def list_replies(self):
        return [self._clean(e) for e in self.replies.query_entities("PartitionKey eq 'r'")]

    def get_setting(self, key):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return json.loads(self.settings.get_entity("s", key)["value"])
        except ResourceNotFoundError:
            return None

    def set_setting(self, key, value):
        self.settings.upsert_entity({"PartitionKey": "s", "RowKey": key, "value": json.dumps(value)})


    # ---------- test copies: replace this (test) data with a copy of the live data ----------
    def replace_from(self, src):
        if self.prefix != "test":
            raise RuntimeError("Refusing to overwrite live data")   # only ever copy live -> test
        counts = {}
        for name in COPIED_TABLES:
            dst_t, src_t = getattr(self, name), getattr(src, name)
            old = [e for e in dst_t.list_entities(select=["PartitionKey", "RowKey"])]
            self._batched(dst_t, "delete", old)
            rows = [e for e in src_t.list_entities()]
            self._batched(dst_t, "upsert", rows)
            counts[name] = len(rows)
        for key in COPIED_SETTINGS:
            value = src.get_setting(key)
            if value is not None:
                self.set_setting(key, value)
        return counts

    @staticmethod
    def _batched(table, op, entities):
        """Table Storage batches must share a PartitionKey and hold at most 100 operations."""
        groups = {}
        for e in entities:
            body = {"PartitionKey": e["PartitionKey"], "RowKey": e["RowKey"]} if op == "delete" else dict(e)
            groups.setdefault(e["PartitionKey"], []).append((op, body))
        for ops in groups.values():
            for i in range(0, len(ops), 100):
                table.submit_transaction(ops[i:i + 100])


class MemoryStore:
    """Same interface as TableStore, for tests and local trials."""
    prefix = ""

    def replace_from(self, src):
        if self.prefix != "test":
            raise RuntimeError("Refusing to overwrite live data")
        import copy
        self.c, self.v, self.p = copy.deepcopy(src.c), copy.deepcopy(src.v), copy.deepcopy(src.p)
        for key in COPIED_SETTINGS:
            if key in src.s:
                self.s[key] = src.s[key]
        return {"customers": len(self.c), "visits": sum(len(x) for x in self.v.values()), "plates": len(self.p)}

    def __init__(self):
        self.c, self.v, self.s, self.p, self.r = {}, {}, {}, {}, {}

    def get_reply(self, key):
        return dict(self.r[key]) if key in self.r else None

    def add_reply(self, key, data):
        self.r[key] = dict(data)

    def list_replies(self):
        return [dict(x) for x in self.r.values()]

    def get_customer(self, phone):
        if phone not in self.c:
            return None
        etag, data = self.c[phone]
        return {**data, "_etag": etag}

    def create_customer(self, phone, data):
        if phone in self.c:
            raise Conflict()
        self.c[phone] = (uuid.uuid4().hex, dict(data))

    def update_customer(self, phone, data, etag):
        if phone not in self.c or self.c[phone][0] != etag:
            raise Conflict()
        self.c[phone] = (uuid.uuid4().hex, dict(data))

    def delete_customer(self, phone):
        self.c.pop(phone, None)

    def list_customers(self):
        return [(k, dict(d)) for k, (_, d) in self.c.items()]

    def add_visit(self, day, row, data):
        self.v.setdefault(day, {})[row] = dict(data)

    def update_visit(self, day, row, patch):
        self.v[day][row].update(patch)

    def list_visits(self, day):
        return [dict(r) for r in self.v.get(day, {}).values()]

    def list_visits_range(self, first_day, last_day):
        return [{**r, "day": d} for d, rows in self.v.items() if first_day <= d <= last_day for r in rows.values()]

    def get_plate(self, plate):
        return self.p.get(plate)

    def set_plate(self, plate, phone):
        self.p[plate] = phone

    def delete_plate(self, plate):
        self.p.pop(plate, None)

    def get_setting(self, key):
        return json.loads(self.s[key]) if key in self.s else None

    def set_setting(self, key, value):
        self.s[key] = json.dumps(value)
