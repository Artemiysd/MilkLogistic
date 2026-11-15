"""База водителей и выданных им маршрутов (SQLite, без внешних зависимостей).

Хранится рядом с данными: ``DB_PATH`` (по умолчанию ``/data/milk.db``).
Таблицы:

    drivers        водители: имя, телефон, закреплённая машина, токен доступа
    assignments    выданный водителю маршрут: прогон, номер рейса, JSON маршрута
    stop_states    статус каждой остановки: ожидает / доставлено / не доставлено
    driver_events  журнал событий водителя: проблемы, перенос, закрытие рейса

Токен — это персональная ссылка водителя ``/driver/<token>``: по ней он видит
только свой маршрут и может отмечать доставку, не заходя в кабинет логиста.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS drivers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT,
    vehicle_id TEXT,
    token TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    dataset_id TEXT,
    route_number INTEGER NOT NULL,
    driver_id TEXT NOT NULL,
    vehicle_id TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'assigned',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stop_states (
    assignment_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    note TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (assignment_id, seq)
);

CREATE TABLE IF NOT EXISTS driver_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id TEXT NOT NULL,
    type TEXT NOT NULL,
    stop_key TEXT,
    text TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assignments_driver ON assignments(driver_id);
CREATE INDEX IF NOT EXISTS idx_assignments_run ON assignments(run_id);
"""

STOP_STATUSES = {"pending", "delivered", "failed"}
PROBLEM_TYPES = {"late", "breakdown", "customer_absent", "refused", "other"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DriverDB:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    # ------------------------------------------------------------ водители

    def list_drivers(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM drivers ORDER BY created_at").fetchall()
        return [self._driver(row) for row in rows]

    def driver(self, driver_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM drivers WHERE id = ?", (driver_id,)).fetchone()
        if row is None:
            raise KeyError("Водитель не найден")
        return self._driver(row)

    def driver_by_token(self, token: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM drivers WHERE token = ?", (token,)).fetchone()
        if row is None:
            raise KeyError("Ссылка водителя недействительна")
        return self._driver(row)

    def create_driver(self, name: str, phone: str | None = None, vehicle_id: str | None = None) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("Укажите имя водителя")
        driver_id = uuid.uuid4().hex[:10]
        token = secrets.token_urlsafe(12)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO drivers (id, name, phone, vehicle_id, token, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (driver_id, name, (phone or "").strip() or None, (vehicle_id or "").strip() or None, token, _now()),
            )
        return self.driver(driver_id)

    def update_driver(self, driver_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"name", "phone", "vehicle_id", "active"}
        updates = {key: value for key, value in fields.items() if key in allowed and value is not None}
        if not updates:
            return self.driver(driver_id)
        if "name" in updates and not str(updates["name"]).strip():
            raise ValueError("Имя водителя не может быть пустым")
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = [int(value) if key == "active" else value for key, value in updates.items()]
        with self._connect() as connection:
            connection.execute(f"UPDATE drivers SET {assignments} WHERE id = ?", (*values, driver_id))
        return self.driver(driver_id)

    def delete_driver(self, driver_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM drivers WHERE id = ?", (driver_id,))

    # ------------------------------------------------------------ маршруты

    def create_assignment(
        self,
        *,
        run_id: str,
        dataset_id: str | None,
        route_number: int,
        driver_id: str,
        vehicle_id: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        assignment_id = uuid.uuid4().hex[:12]
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO assignments "
                "(id, run_id, dataset_id, route_number, driver_id, vehicle_id, payload, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'assigned', ?, ?)",
                (
                    assignment_id,
                    run_id,
                    dataset_id,
                    route_number,
                    driver_id,
                    vehicle_id,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.assignment(assignment_id)

    def assignment(self, assignment_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if row is None:
            raise KeyError("Маршрут не найден")
        return self._assignment(row)

    def list_assignments(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM assignments"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._assignment(row) for row in rows]

    def assignments_for_driver(self, driver_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assignments WHERE driver_id = ? AND status != 'cancelled' "
                "ORDER BY created_at DESC",
                (driver_id,),
            ).fetchall()
        return [self._assignment(row) for row in rows]

    def active_assignment_for_token(self, token: str) -> dict[str, Any]:
        driver = self.driver_by_token(token)
        items = [item for item in self.assignments_for_driver(driver["id"]) if item["status"] in {"assigned", "in_progress"}]
        if not items:
            raise KeyError("Активного маршрута нет")
        return items[0]

    def update_payload(self, assignment_id: str, payload: dict[str, Any], status: str | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            if status:
                connection.execute(
                    "UPDATE assignments SET payload = ?, status = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), status, _now(), assignment_id),
                )
            else:
                connection.execute(
                    "UPDATE assignments SET payload = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), _now(), assignment_id),
                )
        return self.assignment(assignment_id)

    def cancel_assignment(self, assignment_id: str) -> dict[str, Any]:
        return self.update_payload(assignment_id, self.assignment(assignment_id)["payload"], status="cancelled")

    # ------------------------------------------------------------ остановки и события

    def stop_states(self, assignment_id: str) -> dict[int, dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM stop_states WHERE assignment_id = ?", (assignment_id,)
            ).fetchall()
        return {int(row["seq"]): dict(row) for row in rows}

    def set_stop_status(
        self,
        assignment_id: str,
        seq: int,
        status: str,
        *,
        reason: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        if status not in STOP_STATUSES:
            raise ValueError(f"Недопустимый статус остановки: {status}")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO stop_states (assignment_id, seq, status, reason, note, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(assignment_id, seq) DO UPDATE SET "
                "status = excluded.status, reason = excluded.reason, note = excluded.note, "
                "updated_at = excluded.updated_at",
                (assignment_id, int(seq), status, reason, note, _now()),
            )
        self.add_event(
            assignment_id,
            "stop_" + status,
            text=reason or note,
            stop_key=str(seq),
        )
        self._refresh_status(assignment_id)
        return self.assignment(assignment_id)

    def add_event(self, assignment_id: str, event_type: str, *, text: str | None = None, stop_key: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO driver_events (assignment_id, type, stop_key, text, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (assignment_id, event_type, stop_key, text, _now()),
            )

    def events(self, assignment_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM driver_events WHERE assignment_id = ? ORDER BY id DESC LIMIT ?",
                (assignment_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def _refresh_status(self, assignment_id: str) -> None:
        assignment = self.assignment(assignment_id)
        total = len(assignment["payload"].get("stops", []))
        states = self.stop_states(assignment_id)
        resolved = sum(1 for state in states.values() if state["status"] in {"delivered", "failed"})
        if total and resolved >= total:
            status = "completed"
        elif states:
            status = "in_progress"
        else:
            status = assignment["status"]
        with self._connect() as connection:
            connection.execute(
                "UPDATE assignments SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), assignment_id),
            )

    # ------------------------------------------------------------ сборка ответов

    def _driver(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "phone": row["phone"],
            "vehicle_id": row["vehicle_id"],
            "token": row["token"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
            "link": f"/driver/{row['token']}",
        }

    def _assignment(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload"])
        states = self.stop_states(row["id"])
        stops = []
        delivered = 0
        for stop in payload.get("stops", []):
            seq = int(stop.get("seq", 0))
            state = states.get(seq, {})
            status = state.get("status", "pending")
            if status in {"delivered", "failed"}:
                delivered += 1
            stops.append(
                {
                    **stop,
                    "status": status,
                    "reason": state.get("reason"),
                    "note": state.get("note"),
                }
            )
        payload = {**payload, "stops": stops}
        total = len(stops)
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "dataset_id": row["dataset_id"],
            "route_number": row["route_number"],
            "driver_id": row["driver_id"],
            "vehicle_id": row["vehicle_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "payload": payload,
            "progress": {
                "total": total,
                "resolved": delivered,
                "delivered": sum(1 for stop in stops if stop["status"] == "delivered"),
                "failed": sum(1 for stop in stops if stop["status"] == "failed"),
                "pending": total - delivered,
                "percent": round(100 * delivered / total) if total else 0,
            },
        }
