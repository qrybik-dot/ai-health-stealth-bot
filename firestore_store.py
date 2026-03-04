import datetime as dt
import logging
import os
from typing import Any, Dict, Optional

try:
    from google.cloud import firestore
except Exception:  # optional dependency for local dev
    firestore = None

log = logging.getLogger(__name__)


class FirestoreStore:
    def __init__(self) -> None:
        self.project_id = os.getenv("FIRESTORE_PROJECT_ID", "").strip()
        self.enabled = bool(self.project_id and firestore is not None)
        self._client = None
        if self.enabled:
            self._client = firestore.Client(project=self.project_id)

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
