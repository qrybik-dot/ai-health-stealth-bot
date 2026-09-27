import datetime as dt
import json
import logging
import os
from typing import Any, Dict, Optional

try:
    from google.cloud import firestore
    from google.oauth2 import service_account
except Exception:  # optional dependency for local dev
    firestore = None
    service_account = None

log = logging.getLogger(__name__)


def _default_list_days() -> int:
    raw = os.getenv("FIRESTORE_HISTORY_QUERY_DAYS") or os.getenv("CACHE_RETENTION_DAYS") or "365"
    try:
        value = int(raw)
    except ValueError:
        value = 365
    return max(1, min(3650, value))


def _service_account_info() -> Optional[Dict[str, Any]]:
    raw = os.getenv("FIRESTORE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("FIRESTORE_SERVICE_ACCOUNT_JSON must be a JSON object")
    return payload


class FirestoreStore:
    def __init__(self) -> None:
        self.project_id = os.getenv("FIRESTORE_PROJECT_ID", "").strip()
        self.enabled = False
        self.init_error = ""
        self._client = None

        if firestore is None:
            return

        try:
            credentials = None
            info = _service_account_info()
            if info:
                if service_account is None:
                    raise RuntimeError("google.oauth2.service_account is unavailable")
                credentials = service_account.Credentials.from_service_account_info(info)
                if not self.project_id:
                    self.project_id = str(info.get("project_id", "")).strip()

            if not self.project_id:
                return

            kwargs: Dict[str, Any] = {"project": self.project_id}
            if credentials is not None:
                kwargs["credentials"] = credentials
            self._client = firestore.Client(**kwargs)
            self.enabled = True
        except Exception as exc:
            self.init_error = f"{type(exc).__name__}: {exc}"
            self._client = None
            self.enabled = False
            log.warning("firestore_init_failed error=%s", self.init_error)

    def _doc(self, *parts: str):
        if not self.enabled or self._client is None:
            return None
        ref = self._client.collection(parts[0]).document(parts[1])
        idx = 2
        while idx < len(parts):
            ref = ref.collection(parts[idx]).document(parts[idx + 1])
            idx += 2
        return ref

    def get_day(self, chat_id: str, day_key: str) -> Dict[str, Any]:
        doc = self._doc("users", chat_id, "days", day_key)
        if doc is None:
            return {}
        snap = doc.get()
        return snap.to_dict() or {} if snap.exists else {}

    def upsert_day(self, chat_id: str, day_key: str, payload: Dict[str, Any]) -> None:
        doc = self._doc("users", chat_id, "days", day_key)
        if doc is None:
            return
        doc.set(payload, merge=True)

    def list_days(self, chat_id: str, limit: Optional[int] = None, descending: bool = True) -> Dict[str, Dict[str, Any]]:
        if not self.enabled or self._client is None:
            return {}
        safe_limit = max(1, int(limit if limit is not None else _default_list_days()))
        direction = firestore.Query.DESCENDING if descending else firestore.Query.ASCENDING
        days_ref = self._client.collection("users").document(chat_id).collection("days")
        query = days_ref.order_by("__name__", direction=direction).limit(safe_limit)
        out: Dict[str, Dict[str, Any]] = {}
        for snap in query.stream():
            payload = snap.to_dict() if snap.exists else None
            if not isinstance(payload, dict):
                continue
            out[snap.id] = payload
        return out

    def get_sent(self, chat_id: str, key: str) -> Optional[Dict[str, Any]]:
        doc = self._doc("users", chat_id, "sent", key)
        if doc is None:
            return None
        snap = doc.get()
        return snap.to_dict() if snap.exists else None

    def set_sent(self, chat_id: str, key: str, payload: Dict[str, Any]) -> None:
        doc = self._doc("users", chat_id, "sent", key)
        if doc is None:
            return
        doc.set(payload, merge=True)

    def get_auth(self, chat_id: str, provider: str = "garmin") -> Dict[str, Any]:
        doc = self._doc("users", chat_id, "auth", provider)
        if doc is None:
            return {}
        snap = doc.get()
        return snap.to_dict() or {} if snap.exists else {}

    def set_auth(self, chat_id: str, payload: Dict[str, Any], provider: str = "garmin") -> None:
        doc = self._doc("users", chat_id, "auth", provider)
        if doc is None:
            return
        out = dict(payload)
        out["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        doc.set(out, merge=True)


STORE = FirestoreStore()
