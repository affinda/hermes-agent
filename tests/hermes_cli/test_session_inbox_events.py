"""Tests for durable session inbox events.

Session inbox events are trusted internal turns enqueued by external producers
(e.g. DevAgent completion callbacks) and consumed by the gateway watcher.
"""

from __future__ import annotations

import json
import time

import pytest

from hermes_state import SessionDB


class TestSessionInboxEventDB:
    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        home = tmp_path / ".hermes"
        home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(home))
        return SessionDB(db_path=home / "state.db")

    def _make_session(self, db, session_id="sess-1", source="slack"):
        def _do(conn):
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, source, title, started_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, source, "test session", time.time()),
            )

        db._execute_write(_do)

    def test_table_exists(self, db):
        db._conn.execute(
            "SELECT id, source, kind, target_session_id, target_session_key, "
            "source_json, text, metadata_json, delivery_mode, status, attempts, "
            "available_at, locked_until, last_error, response_text FROM session_inbox_events LIMIT 0"
        )

    def test_enqueue_session_inbox_event_creates_queued_row(self, db):
        self._make_session(db, "sess-queue")

        event = db.enqueue_session_inbox_event(
            target_session_id="sess-queue",
            target_session_key="slack:dm:D123:U456",
            source="devagent",
            kind="completion",
            text="Child session completed",
            source_json={"platform": "slack", "chat_id": "D123", "chat_type": "dm", "user_id": "system:devagent"},
            metadata={"child_session_id": "child-1"},
            idempotency_key="devagent:child-1:completed",
        )

        assert event["status"] == "queued"
        assert event["source"] == "devagent"
        assert event["kind"] == "completion"
        assert event["target_session_id"] == "sess-queue"
        assert event["metadata"]["child_session_id"] == "child-1"

        pending = db.list_queued_session_inbox_events()
        assert [row["id"] for row in pending] == [event["id"]]

    def test_enqueue_session_inbox_event_is_idempotent(self, db):
        self._make_session(db, "sess-idem")

        first = db.enqueue_session_inbox_event(
            target_session_id="sess-idem",
            source="devagent",
            kind="completion",
            text="First text",
            idempotency_key="same-key",
        )
        second = db.enqueue_session_inbox_event(
            target_session_id="sess-idem",
            source="devagent",
            kind="completion",
            text="Second text ignored",
            idempotency_key="same-key",
        )

        assert second["id"] == first["id"]
        assert second["text"] == "First text"
        assert len(db.list_queued_session_inbox_events()) == 1

    def test_claim_complete_and_fail_session_inbox_event(self, db):
        event = db.enqueue_session_inbox_event(
            target_session_id="sess-flow",
            source="devagent",
            kind="completion",
            text="Done",
        )

        assert db.claim_session_inbox_event(event["id"], lease_seconds=30) is True
        assert db.claim_session_inbox_event(event["id"], lease_seconds=30) is False

        claimed = db.get_session_inbox_event(event["id"])
        assert claimed["status"] == "processing"
        assert claimed["attempts"] == 1
        assert claimed["locked_until"] > time.time()

        db.complete_session_inbox_event(event["id"])
        completed = db.get_session_inbox_event(event["id"])
        assert completed["status"] == "dispatched"
        assert completed["dispatched_at"] is not None
        assert db.list_queued_session_inbox_events() == []

        failed_event = db.enqueue_session_inbox_event(
            target_session_id="sess-fail",
            source="devagent",
            kind="completion",
            text="Done",
        )
        assert db.claim_session_inbox_event(failed_event["id"], lease_seconds=30) is True
        db.fail_session_inbox_event(failed_event["id"], "temporary problem", retry=True, backoff_seconds=0)
        failed = db.get_session_inbox_event(failed_event["id"])
        assert failed["status"] == "queued"
        assert failed["last_error"] == "temporary problem"
        assert [row["id"] for row in db.list_queued_session_inbox_events()] == [failed_event["id"]]

    def test_expired_processing_session_inbox_event_is_reclaimable(self, db):
        event = db.enqueue_session_inbox_event(
            target_session_id="sess-expired",
            source="devagent",
            kind="completion",
            text="Done",
        )
        assert db.claim_session_inbox_event(event["id"], lease_seconds=1) is True

        def _expire(conn):
            conn.execute(
                "UPDATE session_inbox_events SET locked_until = ? WHERE id = ?",
                (time.time() - 1, event["id"]),
            )

        db._execute_write(_expire)
        assert [row["id"] for row in db.list_queued_session_inbox_events()] == [event["id"]]
        assert db.claim_session_inbox_event(event["id"], lease_seconds=1) is True
        reclaimed = db.get_session_inbox_event(event["id"])
        assert reclaimed["attempts"] == 2

    def test_busy_deferral_does_not_consume_an_attempt(self, db):
        event = db.enqueue_session_inbox_event(
            target_session_id="sess-busy",
            source="devagent",
            kind="completion",
            text="Done",
        )
        assert db.claim_session_inbox_event(event["id"], lease_seconds=30) is True
        db.defer_session_inbox_event(event["id"], "target busy", backoff_seconds=0)

        deferred = db.get_session_inbox_event(event["id"])
        assert deferred["status"] == "queued"
        assert deferred["attempts"] == 0
        assert deferred["last_error"] == "target busy"
        assert db.claim_session_inbox_event(event["id"], lease_seconds=30) is True
        assert db.get_session_inbox_event(event["id"])["attempts"] == 1

    def test_response_ready_is_persisted_for_delivery_retry(self, db):
        event = db.enqueue_session_inbox_event(
            target_session_id="sess-response",
            source="devagent",
            kind="completion",
            text="Done",
        )
        assert db.claim_session_inbox_event(event["id"], lease_seconds=30) is True
        db.mark_session_inbox_event_response_ready(event["id"], "already generated")
        db.fail_session_inbox_event(event["id"], "send failed", retry=True, backoff_seconds=0)

        retry_event = db.list_queued_session_inbox_events()[0]
        assert retry_event["id"] == event["id"]
        assert retry_event["response_text"] == "already generated"

    def test_session_inbox_event_json_fields_round_trip(self, db):
        source_json = {"platform": "slack", "chat_id": "D123", "chat_type": "dm", "thread_id": "177.1"}
        metadata = {"links": ["https://example.invalid/session"], "status": "completed"}
        event = db.enqueue_session_inbox_event(
            target_session_id="sess-json",
            source="devagent",
            kind="completion",
            text="Done",
            source_json=source_json,
            metadata=metadata,
        )

        raw = db._conn.execute(
            "SELECT source_json, metadata_json FROM session_inbox_events WHERE id = ?",
            (event["id"],),
        ).fetchone()
        assert json.loads(raw["source_json"]) == source_json
        assert json.loads(raw["metadata_json"]) == metadata

        fetched = db.get_session_inbox_event(event["id"])
        assert fetched["source_json"] == source_json
        assert fetched["metadata"] == metadata
