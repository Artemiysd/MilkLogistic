"""
API MilkLogistic: данные, планирование маршрутов, геометрия и HTML-отчёты.

    GET  /                          страница проверки (не фронтенд)
    GET  /api/health
    GET  /api/data
    POST /api/optimize              {"drivers": 16, "include_geometry": true, "dataset_id": null}
    GET  /api/runs                  последние прогоны
    GET  /api/runs/{id}             JSON прогона
    GET  /api/runs/{id}/report      HTML-отчёт: карта, маршруты, расшифровка
    GET  /api/runs/{id}/geojson     линии маршрутов
    POST /api/import                выгрузка 1С телом запроса (схема schema.json)
    POST /api/import/file           выгрузка 1С файлом (можно несколько за раз)
    GET  /api/datasets              импортированные наборы
    GET  /api/datasets/{id}         карточка набора
    GET  /api/datasets/{id}/data    данные набора
    POST /api/datasets/active       выбрать активный набор (null — штатные данные)
    DELETE /api/datasets/{id}       удалить набор
    POST /api/demo/seed
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import __version__
from .datasets import DatasetError, DatasetStore, build_matrix, matrix_info
from .db import DriverDB
from .dispatch import build_extra_plan, dispatch_run, dispatch_summary
from .planner_adapter import expand_vehicles, load_json, run_plan
from .report import render_index, render_report

DATA_FILE = Path(os.environ.get("DATA_FILE", "/data/logistics_data.json"))
OVERRIDES_FILE = Path(os.environ.get("OVERRIDES_FILE", "/data/geocode_overrides.json"))
MATRIX_CACHE = Path(os.environ.get("MATRIX_CACHE", "/data/matrix_cache.json"))
RUNS_DIR = Path(os.environ.get("RUNS_DIR", "/data/runs"))
IMPORT_DIR = Path(os.environ.get("IMPORT_DIR", "/data/imports"))
DB_PATH = Path(os.environ.get("DB_PATH", "/data/milk.db"))
ROUTING_URL = os.environ.get("ROUTING_URL", "")

app = FastAPI(
    title="MilkLogistic API",
    version=__version__,
    description=(
        "Планирование доставки по реальной матрице OSRM + импорт выгрузок 1С "
        "+ отчёты для проверки"
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_store: DatasetStore | None = None
_db: DriverDB | None = None


def store() -> DatasetStore:
    global _store
    if _store is None:
        _store = DatasetStore(IMPORT_DIR)
    return _store


def db() -> DriverDB:
    global _db
    if _db is None:
        _db = DriverDB(DB_PATH)
    return _db


class OptimizeRequest(BaseModel):
    drivers: int | None = Field(default=None, ge=1, le=100, description="Сколько водителей доступно сегодня")
    shift_end: str = Field(default="17:00", description="Конец смены, HH:MM")
    loading_minutes: int = Field(default=30, ge=0, le=180)
    include_geometry: bool = Field(default=True, description="Считать линии маршрутов через сервис маршрутизации")
    dataset_id: str | None = Field(default=None, description="Набор из /api/import; по умолчанию активный")
    enforce_min_load: bool = Field(
        default=False,
        description="Не выпускать машины с загрузкой ниже min_load_ratio (рейсы уходят в «не развезено»)",
    )


class ActiveDatasetRequest(BaseModel):
    dataset_id: str | None = Field(default=None, description="null — вернуться к штатным данным")


class DriverIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    vehicle_id: str | None = Field(default=None, max_length=80)


class DriverPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    vehicle_id: str | None = Field(default=None, max_length=80)
    active: bool | None = None


class DispatchRequest(BaseModel):
    assignments: dict[int, str] | None = Field(
        default=None,
        description="Необязательно: {номер рейса: id водителя}. Без словаря водители подбираются автоматически",
    )
    allow_underloaded: bool = Field(
        default=False,
        description="Отправлять рейсы с загрузкой ниже 90% (решение логиста)",
    )
    exclude_routes: list[int] | None = Field(
        default=None,
        description="Номера рейсов, которые логист решил не отправлять",
    )


class StopStatusIn(BaseModel):
    status: Literal["delivered", "failed"]
    reason: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class ProblemIn(BaseModel):
    type: Literal["late", "breakdown", "customer_absent", "refused", "other"] = "other"
    text: str | None = Field(default=None, max_length=500)


class ExtraWaveRequest(BaseModel):
    dataset_id: str | None = Field(default=None, description="Свежая выгрузка 1С для доп. заявок")
    start_time: str = Field(default="13:00", description="Старт второй волны, HH:MM")
    shift_end: str = Field(default="17:00")
    max_vehicles: int | None = Field(default=None, ge=1, le=50)


def _base_data() -> dict:
    if not DATA_FILE.exists():
        raise HTTPException(500, f"Нет файла данных {DATA_FILE}")
    data = load_json(DATA_FILE)
    # Ручные правки координат (битые геокоды) — те же, что применяет сборщик матрицы.
    if OVERRIDES_FILE.exists():
        fixes = {
            item["point_id"]: item
            for item in load_json(OVERRIDES_FILE).get("overrides", [])
        }
        for point in data.get("delivery_points", []):
            fix = fixes.get(point.get("id"))
            if fix:
                point["coords"] = fix["coords"]
                point["geocode_quality"] = fix.get("quality", "manual")
    return data


def _dataset(dataset_id: str | None) -> tuple[dict, str | None]:
    """Данные набора: явный dataset_id, иначе активный, иначе штатный DATA_FILE."""
    try:
        resolved = dataset_id or store().active()
        if resolved:
            return store().load_data(resolved), resolved
    except DatasetError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _base_data(), None


def _base_matrix() -> dict:
    if not MATRIX_CACHE.exists():
        raise HTTPException(
            503,
            f"Нет матрицы {MATRIX_CACHE}. Соберите её: docker compose --profile tools run --rm matrix",
        )
    return load_json(MATRIX_CACHE)


def _matrix(dataset_id: str | None = None) -> dict:
    """Матрица набора, если она собрана при импорте, иначе базовая."""
    if dataset_id:
        cache = store().matrix(dataset_id)
        if cache:
            return cache
    return _base_matrix()


@app.get("/api/health")
def health() -> dict:
    data, dataset_id = _dataset(None)
    points = data.get("delivery_points", [])
    vehicles = expand_vehicles(data.get("vehicles", []))
    cache = _matrix(dataset_id)
    matrix_ready = bool(cache.get("size"))
    meta = store().meta(dataset_id) if dataset_id else None
    matrix_source = "dataset" if dataset_id and store().matrix(dataset_id) else "base"
    return {
        "status": "ok" if matrix_ready else "degraded",
        "version": __version__,
        "mode": "real",
        "planner": "logistics.RoutePlanner",
        "routing_url": ROUTING_URL or None,
        "points": len(points),
        "addresses": len({p["address"].strip().lower() for p in points}),
        "vehicles": len(vehicles),
        "demand_kg": sum(float(p.get("demand", 0)) for p in points),
        "matrix": {
            "ready": matrix_ready,
            "size": cache.get("size"),
            "provider": cache.get("provider"),
            "source": matrix_source,
        },
        "drivers": len(db().list_drivers()),
        "dataset": {
            "active": dataset_id,
            "name": meta["name"] if meta else "Штатные данные (logistics_data.json)",
            "imported": len(store().list()),
            "with_coords": meta.get("with_coords") if meta else len(points),
            "depot_coords": meta.get("depot_coords") if meta else True,
        },
    }


@app.get("/api/data")
def get_data(dataset_id: str | None = Query(default=None)) -> dict:
    data, resolved = _dataset(dataset_id)
    try:
        name = store().meta(resolved)["name"] if resolved else "Штатные данные (logistics_data.json)"
    except DatasetError:
        name = "Штатные данные (logistics_data.json)"
    return {
        "dataset_id": resolved,
        "dataset": {"id": resolved, "name": name},
        "depot": data["depot"],
        "vehicles": expand_vehicles(data.get("vehicles", [])),
        "vehicle_types": data.get("vehicles", []),
        "delivery_points": data.get("delivery_points", []),
        "constraints": data.get("constraints", {}),
    }


@app.post("/api/optimize")
def optimize(body: OptimizeRequest | None = None) -> dict:
    body = body or OptimizeRequest()
    data, dataset_id = _dataset(body.dataset_id)
    cache = _matrix(dataset_id)
    try:
        result = run_plan(
            data,
            cache,
            drivers=body.drivers,
            shift_end=body.shift_end,
            loading_minutes=body.loading_minutes,
            routing_url=ROUTING_URL,
            include_geometry=body.include_geometry,
            enforce_min_load=body.enforce_min_load,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    result["dataset_id"] = dataset_id
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / f"{result['run_id']}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        # результат важнее сохранения, но молчать нельзя: иначе отчёт отдаёт 404
        result["warnings"].append(f"Прогон не сохранён ({RUNS_DIR}): {exc}")
    return result


# ---------------------------------------------------------------- импорт 1С


def _import_payload(
    payload: dict,
    *,
    name: str | None,
    activate: bool,
    build: bool,
) -> dict:
    try:
        meta = store().save(payload, name=name)
    except DatasetError as exc:
        raise HTTPException(422, str(exc)) from exc

    info: dict = {"ready": False, "size": None, "provider": None, "built_at": None}
    error: str | None = None
    if build:
        try:
            cache = build_matrix(store().load_data(meta["id"]), ROUTING_URL)
            store().save_matrix(meta["id"], cache)
            info = matrix_info(cache)
        except DatasetError as exc:
            error = str(exc)

    active = store().activate(meta["id"]) if activate else store().active()
    return {
        "status": "ok",
        "dataset": meta,
        "matrix": {**info, "error": error},
        "active": active,
        "activated": activate,
    }


@app.post("/api/import", status_code=201)
def import_dataset(
    payload: dict = Body(..., description="Выгрузка 1С по контракту schema.json"),
    name: str | None = Query(default=None, description="Имя набора (по умолчанию из meta/даты)"),
    activate: bool = Query(default=True, description="Сразу сделать набор активным"),
    build_matrix: bool = Query(default=True, description="Собрать дорожную матрицу через routing"),
) -> dict:
    return _import_payload(payload, name=name, activate=activate, build=build_matrix)


@app.post("/api/import/file", status_code=201)
def import_dataset_files(
    files: list[UploadFile] = File(..., description="JSON-выгрузки 1С (можно несколько)"),
    activate: bool = Query(default=True, description="Сделать последний успешный набор активным"),
    build_matrix: bool = Query(default=True, description="Собрать дорожную матрицу через routing"),
) -> dict:
    results: list[dict] = []
    errors: list[dict] = []
    active: str | None = store().active()

    for upload in files:
        filename = upload.filename or "1c.json"
        try:
            raw = upload.file.read()
            if isinstance(raw, bytes):
                text = raw.decode("utf-8-sig")
            else:  # pragma: no cover - на случай нестандартного file-like
                text = str(raw)
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError("Корень JSON должен быть объектом {...}")
        except (UnicodeDecodeError, ValueError) as exc:
            errors.append({"file": filename, "error": f"JSON не прочитан: {exc}"})
            continue

        try:
            result = _import_payload(
                payload,
                name=filename,
                activate=False,
                build=build_matrix,
            )
        except HTTPException as exc:
            errors.append({"file": filename, "error": str(exc.detail)})
            continue
        result["file"] = filename
        results.append(result)
        active = result["dataset"]["id"]

    if activate and results:
        active = store().activate(active)
    if not results:
        raise HTTPException(422, {"message": "Ни один файл не импортирован", "errors": errors})

    return {
        "status": "ok" if not errors else "partial",
        "imported": len(results),
        "datasets": [item["dataset"] for item in results],
        "results": results,
        "errors": errors,
        "active": active,
    }


@app.get("/api/datasets")
def list_datasets() -> dict:
    items = store().list()
    return {"count": len(items), "active": store().active(), "datasets": items}


@app.post("/api/datasets/active")
def set_active_dataset(body: ActiveDatasetRequest) -> dict:
    try:
        active = store().activate(body.dataset_id)
    except DatasetError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"status": "ok", "active": active}


@app.get("/api/datasets/{dataset_id}")
def dataset_info(dataset_id: str) -> dict:
    try:
        meta = store().meta(dataset_id)
        data = store().load_data(dataset_id)
    except DatasetError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        **meta,
        "matrix": matrix_info(store().matrix(dataset_id)),
        "active": store().active() == dataset_id,
        "depot": data.get("depot"),
        "constraints": data.get("constraints", {}),
    }


@app.get("/api/datasets/{dataset_id}/data")
def dataset_data(dataset_id: str) -> dict:
    try:
        return store().load_data(dataset_id)
    except DatasetError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/api/datasets/{dataset_id}")
def delete_dataset(dataset_id: str) -> dict:
    try:
        meta = store().delete(dataset_id)
    except DatasetError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"status": "ok", "deleted": meta["id"], "active": store().active()}


# ---------------------------------------------------------------- водители


def _public_assignment(assignment: dict) -> dict:
    try:
        driver = db().driver(assignment["driver_id"])
    except KeyError:
        driver = None
    return {**assignment, "driver": driver, "link": driver["link"] if driver else None}


@app.get("/api/drivers")
def list_drivers() -> dict:
    items = db().list_drivers()
    return {"count": len(items), "drivers": items}


@app.post("/api/drivers", status_code=201)
def create_driver(body: DriverIn) -> dict:
    try:
        return db().create_driver(body.name, body.phone, body.vehicle_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.patch("/api/drivers/{driver_id}")
def update_driver(driver_id: str, body: DriverPatch) -> dict:
    try:
        return db().update_driver(driver_id, **body.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/api/drivers/unassigned")
def cleanup_drivers() -> dict:
    """Удаляет водителей, у которых нет выданных маршрутов (чистка базы)."""
    busy = {
        item["driver_id"]
        for item in db().list_assignments()
        if item["status"] != "cancelled"
    }
    deleted = []
    for driver in db().list_drivers():
        if driver["id"] not in busy:
            db().delete_driver(driver["id"])
            deleted.append(driver["id"])
    return {"status": "ok", "deleted": len(deleted), "left": len(db().list_drivers())}


@app.delete("/api/drivers/{driver_id}")
def delete_driver(driver_id: str) -> dict:
    try:
        db().driver(driver_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    db().delete_driver(driver_id)
    return {"status": "ok", "deleted": driver_id}


@app.post("/api/drivers/seed")
def seed_drivers() -> dict:
    """Создаёт по водителю на каждую машину активного набора (если ещё нет)."""
    data, _ = _dataset(None)
    existing = {item["vehicle_id"] for item in db().list_drivers() if item["vehicle_id"]}
    created = []
    for vehicle in expand_vehicles(data.get("vehicles", [])):
        if vehicle["id"] in existing:
            continue
        created.append(db().create_driver(f"Водитель {len(existing) + 1}", vehicle_id=vehicle["id"]))
        existing.add(vehicle["id"])
    return {"status": "ok", "created": created, "count": len(created)}


# ---------------------------------------------------------------- выдача маршрутов


def _load_run(run_id: str) -> dict:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, "Прогон не найден")
    return load_json(path)


def _dataset_exact(dataset_id: str | None) -> dict:
    if dataset_id:
        try:
            return store().load_data(dataset_id)
        except DatasetError as exc:
            raise HTTPException(404, str(exc)) from exc
    return _base_data()


@app.post("/api/runs/{run_id}/dispatch")
def dispatch(run_id: str, body: DispatchRequest | None = None) -> dict:
    """Раздаёт рейсы прогона водителям. Без водителей создаёт их по числу рейсов.

    По умолчанию рейсы с загрузкой ниже 90% не отправляются: решение за логистом
    (``allow_underloaded`` или ``exclude_routes``).
    """
    body = body or DispatchRequest()
    run = _load_run(run_id)
    if not run.get("routes"):
        raise HTTPException(409, "В прогоне нет маршрутов")

    excluded = set(body.exclude_routes or [])
    routes = [route for route in run["routes"] if int(route.get("route_number", 0)) not in excluded]
    skipped = []
    if not body.allow_underloaded:
        allowed = []
        for route in routes:
            if route.get("underloaded"):
                skipped.append(
                    {
                        "route_number": route.get("route_number"),
                        "vehicle_id": route.get("vehicle_id"),
                        "load_kg": route.get("load_kg"),
                        "load_ratio": route.get("load_ratio"),
                        "reason": "загрузка ниже 90% — нужно решение логиста",
                    }
                )
            else:
                allowed.append(route)
        routes = allowed
    if not routes:
        raise HTTPException(
            409,
            {
                "message": "Нечего отправлять: все рейсы исключены или недогружены",
                "skipped": skipped,
            },
        )
    run = {**run, "routes": routes}

    existing = [
        item
        for item in db().list_assignments()
        if item["run_id"] == run_id and item["status"] != "cancelled"
    ]
    if existing:
        return {
            "status": "ok",
            "already": True,
            "count": len(existing),
            "assignments": [_public_assignment(item) for item in existing],
            "skipped": skipped,
        }

    drivers = [item for item in db().list_drivers() if item["active"]]
    existing_vehicles = {item["vehicle_id"] for item in drivers if item.get("vehicle_id")}
    for route in routes:
        vehicle_id = route.get("vehicle_id")
        if vehicle_id and vehicle_id not in existing_vehicles:
            db().create_driver(f"Водитель {len(drivers) + 1}", vehicle_id=vehicle_id)
            existing_vehicles.add(vehicle_id)
            drivers = [item for item in db().list_drivers() if item["active"]]
    drivers = [item for item in db().list_drivers() if item["active"]]

    created = dispatch_run(run, drivers, db(), body.assignments)
    if not created:
        raise HTTPException(409, "Не хватило водителей: добавьте их в базу и повторите")
    return {
        "status": "ok",
        "count": len(created),
        "assignments": [_public_assignment(item) for item in created],
        "skipped": skipped,
    }


@app.post("/api/runs/{run_id}/rebalance")
def rebalance_run(run_id: str) -> dict:
    """Пересчитывает прогон с жёстким правилом 90%: недогруженные рейсы не выезжают."""
    run = _load_run(run_id)
    data = _dataset_exact(run.get("dataset_id"))
    cache = _matrix(run.get("dataset_id"))
    options = run.get("options", {})
    try:
        result = run_plan(
            data,
            cache,
            drivers=options.get("drivers"),
            shift_end=options.get("shift_end") or "17:00",
            loading_minutes=int(options.get("loading_minutes") or 30),
            routing_url=ROUTING_URL,
            include_geometry=bool(options.get("include_geometry", True)),
            enforce_min_load=True,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    result["dataset_id"] = run.get("dataset_id")
    result["parent_run_id"] = run_id
    result["rebalanced_from"] = run_id
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / f"{result['run_id']}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass
    return result


@app.get("/api/dispatch")
def dispatch_overview(run_id: str | None = Query(default=None), all: bool = Query(default=False)) -> dict:
    """Сводка по смене: по умолчанию только последний выданный прогон."""
    assignments = db().list_assignments()
    drivers = db().list_drivers()

    current_run = None
    if not all:
        latest = max(assignments, key=lambda item: item["created_at"], default=None)
        selected = run_id or (latest["run_id"] if latest else None)
        if selected:
            assignments = [item for item in assignments if item["run_id"] == selected]
            run_path = RUNS_DIR / f"{selected}.json"
            fleet: set[str] = set()
            if run_path.exists():
                run = load_json(run_path)
                fleet = {str(route.get("vehicle_id")) for route in run.get("routes", [])}
                dataset_name = "Штатные данные"
                if run.get("dataset_id"):
                    try:
                        dataset_name = store().meta(run["dataset_id"])["name"]
                    except DatasetError:
                        dataset_name = run["dataset_id"]
                current_run = {
                    "run_id": selected,
                    "created_at": run.get("created_at"),
                    "dataset_id": run.get("dataset_id"),
                    "dataset_name": dataset_name,
                    "routes": len(run.get("routes", [])),
                }
            assigned_to = {item["driver_id"] for item in assignments}
            drivers = [
                item
                for item in drivers
                if item["id"] in assigned_to or (fleet and item.get("vehicle_id") in fleet)
            ]

    payload = dispatch_summary(db(), assignments, drivers)
    payload["assignments"] = [_public_assignment(item) for item in payload["assignments"]]
    payload["current_run"] = current_run
    return payload


@app.post("/api/dispatch/reset")
def reset_shift() -> dict:
    """Начать смену с чистого листа: отменить маршруты и удалить водителей."""
    assignments = db().list_assignments()
    cancelled = 0
    for item in assignments:
        if item["status"] != "cancelled":
            db().cancel_assignment(item["id"])
            cancelled += 1
    drivers = db().list_drivers()
    for driver in drivers:
        db().delete_driver(driver["id"])
    return {
        "status": "ok",
        "cancelled": cancelled,
        "deleted_drivers": len(drivers),
    }


@app.post("/api/assignments/{assignment_id}/cancel")
def cancel_assignment(assignment_id: str) -> dict:
    try:
        item = db().cancel_assignment(assignment_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    return {"status": "ok", "assignment": _public_assignment(item)}


@app.post("/api/assignments/{assignment_id}/replan")
def replan_assignment(assignment_id: str) -> dict:
    """Пересчитывает невыполненные точки маршрута с текущего момента (водитель не успевает)."""
    try:
        assignment = db().assignment(assignment_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc

    payload = assignment["payload"]
    pending = [stop for stop in payload.get("stops", []) if stop.get("status") == "pending"]
    if not pending:
        raise HTTPException(409, "Все остановки уже закрыты — пересчитывать нечего")
    capacity = int(payload.get("vehicle_capacity") or 0)
    vehicle_id = payload.get("vehicle_id")
    if capacity <= 0 or not vehicle_id:
        raise HTTPException(409, "В маршруте не указана машина")

    points = []
    for stop in pending:
        coords = stop.get("coords")
        orders = stop.get("orders") or []
        if not orders:
            orders = [
                {
                    "id": stop.get("point_id") or f"stop-{stop.get('seq')}",
                    "name": stop.get("address"),
                    "demand_kg": stop.get("demand_kg"),
                }
            ]
        for order in orders:
            points.append(
                {
                    "id": str(order.get("id") or f"stop-{stop.get('seq')}"),
                    "name": str(order.get("name") or stop.get("address") or ""),
                    "address": str(stop.get("address") or ""),
                    "street": str(stop.get("street") or ""),
                    "demand": float(order.get("demand_kg") or stop.get("demand_kg") or 0),
                    "coords": coords,
                }
            )

    source = _dataset_exact(assignment["dataset_id"])
    derived = {
        "depot": source.get("depot"),
        "vehicles": [
            {
                "id": vehicle_id,
                "name": payload.get("vehicle_type") or vehicle_id,
                "capacity": capacity,
                "count": 1,
                "restricted_to_small_streets": capacity <= 1500,
            }
        ],
        "delivery_points": points,
        "constraints": {
            **source.get("constraints", {}),
            "workday_start": datetime.now().strftime("%H:%M"),
            "min_load_ratio": 0,
            "one_trip_per_vehicle": True,
        },
    }
    try:
        matrix = build_matrix(derived, ROUTING_URL)
    except DatasetError:
        matrix = _matrix(assignment["dataset_id"])

    result = run_plan(
        derived,
        matrix,
        drivers=1,
        routing_url=ROUTING_URL,
        include_geometry=True,
    )
    route = result["routes"][0] if result.get("routes") else None
    if route is None:
        raise HTTPException(409, "Не удалось пересчитать маршрут: в смену уже ничего не влезает")

    delivered = [stop for stop in payload.get("stops", []) if stop.get("status") != "pending"]
    next_seq = max([int(stop.get("seq", 0)) for stop in payload.get("stops", [])] + [0]) + 1
    new_stops = []
    for stop in route.get("stops", []):
        new_stops.append({**stop, "seq": next_seq, "status": "pending"})
        next_seq += 1

    delivered_keys = {stop.get("point_id") for stop in delivered}
    left_pending = [
        stop
        for stop in pending
        if stop.get("point_id") not in {item.get("point_id") for item in route.get("stops", [])}
        and stop.get("point_id") not in delivered_keys
    ]

    updated = {
        **payload,
        "stops": delivered + new_stops + left_pending,
        "geometry": route.get("geometry"),
        "remaining_geometry": route.get("geometry"),
        "remaining_distance_m": route.get("distance_m"),
        "replanned_at": datetime.now().isoformat(timespec="seconds"),
        "route_number": payload.get("route_number"),
    }
    db().update_payload(assignment_id, updated, status="in_progress")
    db().add_event(
        assignment_id,
        "replan",
        text=f"Маршрут пересчитан: {len(new_stops)} точек, осталось {len(left_pending)}",
    )
    return {
        "status": "ok",
        "assignment": _public_assignment(db().assignment(assignment_id)),
        "replanned_stops": len(new_stops),
        "left_pending": len(left_pending),
        "warnings": result.get("warnings", []),
    }


# ---------------------------------------------------------------- доп. заезд


@app.post("/api/runs/{run_id}/extra")
def extra_wave(run_id: str, body: ExtraWaveRequest | None = None) -> dict:
    """Вторая волна: остаток прогона + (опционально) свежая выгрузка 1С."""
    body = body or ExtraWaveRequest()
    run = _load_run(run_id)
    data = _dataset_exact(run.get("dataset_id"))
    extra_data = None
    if body.dataset_id:
        try:
            extra_data = store().load_data(body.dataset_id)
        except DatasetError as exc:
            raise HTTPException(404, str(exc)) from exc

    try:
        result = build_extra_plan(
            run,
            data,
            ROUTING_URL,
            start_time=body.start_time,
            extra_data=extra_data,
            max_vehicles=body.max_vehicles,
            shift_end=body.shift_end,
            fallback_matrix=_matrix(run.get("dataset_id")),
        )
    except DatasetError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / f"{result['run_id']}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass
    return result


# ---------------------------------------------------------------- водитель


def _refresh_route_geometry(assignment: dict) -> dict:
    """Пересобирает линию маршрута только по оставшимся точкам.

    Когда водитель закрыл точку, путь до неё и сама точка пропадают с карты:
    линия строится от последней закрытой точки (или склада) через оставшиеся
    и обратно на склад.
    """
    payload = assignment["payload"]
    stops = payload.get("stops", [])
    pending = [
        stop for stop in stops
        if stop.get("status", "pending") == "pending" and stop.get("coords")
    ]
    if not pending:
        payload["remaining_geometry"] = None
        payload["remaining_distance_m"] = 0.0
        return payload

    depot = (payload.get("depot") or {}).get("coords") or {}
    resolved = [
        stop for stop in stops
        if stop.get("status") in {"delivered", "failed"} and stop.get("coords")
    ]
    origin = max(resolved, key=lambda stop: int(stop.get("seq", 0)))["coords"] if resolved else depot

    sequence = []
    if origin:
        sequence.append(origin)
    sequence.extend(stop["coords"] for stop in pending)
    if depot:
        sequence.append(depot)

    points = [
        {"id": str(index), "lat": float(coords["lat"]), "lon": float(coords["lon"])}
        for index, coords in enumerate(sequence)
    ]
    if not ROUTING_URL or len(points) < 2:
        payload["remaining_geometry"] = None
        return payload

    request = urllib.request.Request(
        ROUTING_URL.rstrip("/") + "/api/calculate-route",
        data=json.dumps({"points": points, "return_geometry": True}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            route = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError):
        payload["remaining_geometry"] = None
        return payload

    payload["remaining_geometry"] = route.get("geometry")
    payload["remaining_distance_m"] = route.get("distance_m")
    return payload


@app.get("/api/driver/{token}")
def driver_view(token: str) -> dict:
    try:
        assignment = db().active_assignment_for_token(token)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    except ValueError as exc:  # pragma: no cover
        raise HTTPException(404, str(exc)) from exc
    return {
        "driver": db().driver_by_token(token),
        "assignment": _public_assignment(assignment),
        "events": db().events(assignment["id"]),
    }


@app.post("/api/driver/{token}/stops/{seq}")
def driver_stop(token: str, seq: int, body: StopStatusIn) -> dict:
    try:
        assignment = db().active_assignment_for_token(token)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    if body.status == "failed" and not (body.reason or body.note):
        raise HTTPException(422, "Укажите причину, почему точка не доставлена")
    db().set_stop_status(assignment["id"], seq, body.status, reason=body.reason, note=body.note)
    updated = db().assignment(assignment["id"])
    updated["payload"] = _refresh_route_geometry(updated)
    db().update_payload(assignment["id"], updated["payload"])
    return {"status": "ok", "assignment": _public_assignment(db().assignment(assignment["id"]))}


@app.post("/api/driver/{token}/problem")
def driver_problem(token: str, body: ProblemIn) -> dict:
    try:
        assignment = db().active_assignment_for_token(token)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    db().add_event(assignment["id"], f"problem:{body.type}", text=body.text)
    db().update_payload(assignment["id"], assignment["payload"], status="problem")
    return {"status": "ok", "events": db().events(assignment["id"])}



def _runs() -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    items = sorted(RUNS_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return [load_json(path) for path in items[:10]]


@app.get("/api/runs")
def list_runs() -> dict:
    runs = _runs()
    return {"count": len(runs), "runs": runs}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, "Прогон не найден")
    return load_json(path)


@app.get("/api/runs/{run_id}/report", response_class=HTMLResponse)
def get_report(run_id: str) -> str:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, "Прогон не найден")
    return render_report(load_json(path))


@app.get("/api/runs/{run_id}/geojson")
def get_geojson(run_id: str) -> dict:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, "Прогон не найден")
    run = load_json(path)
    features = []
    for route in run.get("routes", []):
        geometry = route.get("geometry")
        if not geometry:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "route": route.get("route_number"),
                    "vehicle_id": route.get("vehicle_id"),
                    "distance_m": route.get("distance_m"),
                    "load_kg": route.get("load_kg"),
                    "stops": len(route.get("stops", [])),
                },
                "geometry": geometry,
            }
        )
    return {"type": "FeatureCollection", "features": features}


@app.post("/api/demo/seed")
def demo_seed() -> dict:
    data, dataset_id = _dataset(None)
    return {
        "status": "ok",
        "dataset_id": dataset_id,
        "points": len(data.get("delivery_points", [])),
        "vehicles": len(expand_vehicles(data.get("vehicles", []))),
        "message": "Данные читаются из DATA_FILE или импортированного набора, матрица — из кэша",
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Страница проверки: запустить расчёт и открыть отчёт. Это бэкенд, не фронтенд."""
    try:
        health_payload = health()
    except HTTPException as exc:
        health_payload = {"status": f"error: {exc.detail}"}
    return render_index(_runs(), health_payload)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
