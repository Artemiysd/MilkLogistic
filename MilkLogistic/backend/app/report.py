"""
HTML-отчёт по прогону: карта с маршрутами, сводка и расшифровка цифр.

Нужен, чтобы можно было глазами проверить, что и как посчитано, не подключая
фронтенд: страница целиком собирается на бэкенде и отдаётся как один файл.
Карта — Leaflet с CDN: без интернета останутся таблицы и пояснения.
"""

from __future__ import annotations

import html
import json
from datetime import datetime


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _km(meters: float | int | None) -> str:
    if meters is None:
        return "—"
    return f"{meters / 1000:.1f} км"


def _minutes(value: int | float | None) -> str:
    if value is None:
        return "—"
    total = int(round(value))
    return f"{total // 60} ч {total % 60:02d} мин"


STYLE = """
:root { --blue:#1d4ed8; --ink:#1f2933; --muted:#6b7280; --line:#e5e7eb; --bg:#f5f7f6; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.45 system-ui, "Segoe UI", Arial, sans-serif; }
header { background:#fff; border-bottom:1px solid var(--line); padding:18px 28px; }
header h1 { margin:0; font-size:22px; color:var(--blue); }
header p { margin:4px 0 0; color:var(--muted); font-size:13px; }
main { padding:20px 28px 48px; max-width:1400px; margin:0 auto; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin-bottom:16px; }
.card { background:#fff; border-radius:10px; padding:14px 16px; box-shadow:0 1px 3px rgba(0,0,0,.05); }
.card .label { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
.card .value { font-size:26px; font-weight:600; margin-top:6px; }
.card.good .value { color:#047857; }
.panel { background:#fff; border-radius:10px; padding:16px 18px; margin-bottom:16px; box-shadow:0 1px 3px rgba(0,0,0,.05); }
.panel h2 { margin:0 0 12px; font-size:17px; color:var(--blue); }
#map { height:560px; border-radius:8px; }
.legend { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
.chip { display:inline-flex; align-items:center; gap:6px; border:1px solid var(--line); border-radius:999px;
        padding:4px 10px; font-size:12px; cursor:pointer; background:#fff; }
.chip.active { border-color:var(--blue); box-shadow:0 0 0 2px rgba(29,78,216,.15); }
.dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th, td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; }
tr:hover td { background:#fafafa; }
details { border:1px solid var(--line); border-radius:8px; padding:0; margin-bottom:10px; background:#fff; }
details > summary { cursor:pointer; padding:10px 14px; font-weight:600; }
details[open] > summary { border-bottom:1px solid var(--line); }
.details-body { padding:8px 14px 14px; }
.bar { height:8px; background:#eef2f7; border-radius:4px; overflow:hidden; min-width:90px; }
.bar > span { display:block; height:100%; background:#2563eb; }
.note { color:var(--muted); font-size:12px; }
.warn { background:#fffbeb; border:1px solid #fde68a; border-radius:8px; padding:12px 14px; margin-bottom:10px; font-size:13px; }
.err { background:#fef2f2; border:1px solid #fecaca; border-radius:8px; padding:12px 14px; margin-bottom:10px; font-size:13px; }
code { background:#f3f4f6; padding:1px 5px; border-radius:4px; font-size:12px; }
a { color:var(--blue); }
.toolbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }
input[type=number] { width:90px; padding:6px 8px; border:1px solid var(--line); border-radius:6px; }
button { background:var(--blue); color:#fff; border:0; border-radius:8px; padding:9px 16px; font-size:14px; cursor:pointer; }
button:disabled { opacity:.6; cursor:default; }
@media (max-width:820px) { main { padding:14px; } #map { height:380px; } }
"""

PALETTE = [
    "#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0891b2",
    "#be123c", "#4d7c0f", "#9333ea", "#b45309", "#0369a1", "#15803d",
    "#a21caf", "#c2410c", "#0f766e", "#4338ca",
]


def render_report(run: dict) -> str:
    totals = run.get("totals", {})
    baseline = run.get("baseline") or {}
    savings = run.get("savings") or {}
    options = run.get("options", {})
    routes = run.get("routes", [])
    unassigned = run.get("unassigned", [])
    warnings = run.get("warnings", [])
    violations = run.get("violations", [])

    depot = run.get("depot") or {}
    depot_coords = depot.get("coords") or {}
    depot = {"lat": depot_coords.get("lat"), "lon": depot_coords.get("lon")}

    savings_pct = savings.get("distance_pct")
    savings_value = (
        f"{savings_pct:.1f} %" if isinstance(savings_pct, (int, float)) else "—"
    )
    delta = savings.get("distance_m")

    rows = []
    for index, route in enumerate(routes):
        color = PALETTE[index % len(PALETTE)]
        load_pct = round(float(route.get("load_ratio", 0)) * 100)
        stops_rows = "".join(
            "<tr>"
            f"<td>{_e(stop.get('seq'))}</td>"
            f"<td>{_e(stop.get('address'))}</td>"
            f"<td>{_e(stop.get('demand_kg'))}</td>"
            f"<td>{_e(stop.get('arrive'))}</td>"
            f"<td>{_e(stop.get('leave'))}</td>"
            f"<td>{_e(stop.get('wait_minutes'))}</td>"
            f"<td>{_e(', '.join(str(i) for i in (stop.get('point_ids') or [])))}</td>"
            "</tr>"
            for stop in route.get("stops", [])
        )
        rows.append(
            f"<tr data-route='{index}'>"
            f"<td><span class='dot' style='background:{color}'></span> {_e(route.get('route_number'))}</td>"
            f"<td>{_e(route.get('vehicle_id'))}<div class='note'>{_e(route.get('vehicle_type'))}</div></td>"
            f"<td>{_e(route.get('driver'))}"
            f"<div class='note'>рейс {_e(route.get('trip_no'))}</div></td>"
            f"<td>{_e(route.get('load_kg'))} / {_e(route.get('vehicle_capacity'))} кг"
            f"<div class='bar'><span style='width:{min(load_pct, 100)}%'></span></div>"
            f"<div class='note'>{load_pct} %</div></td>"
            f"<td>{_km(route.get('distance_m'))}</td>"
            f"<td>{_minutes(route.get('work_minutes'))}</td>"
            f"<td>{_e(route.get('end_time'))}</td>"
            f"<td>{len(route.get('stops', []))}</td>"
            f"</tr>"
            f"<tr><td colspan='8' style='padding:0;border:0'>"
            f"<details><summary style='font-size:12px;color:#6b7280'>Точки маршрута {_e(route.get('route_number'))}</summary>"
            f"<div class='details-body'><table><thead><tr><th>#</th><th>Адрес</th><th>кг</th>"
            f"<th>Прибытие</th><th>Выезд</th><th>Ожидание</th><th>Заявки</th></tr></thead>"
            f"<tbody>{stops_rows}</tbody></table></div></details></td></tr>"
        )

    unassigned_html = ""
    if unassigned:
        unassigned_rows = "".join(
            f"<tr><td>{_e(item.get('address'))}</td><td>{_e(item.get('demand_kg'))}</td>"
            f"<td>{_e(', '.join(str(i) for i in (item.get('point_ids') or [])))}</td></tr>"
            for item in unassigned
        )
        unassigned_html = (
            "<div class='panel'><h2>Не развезено</h2>"
            "<table><thead><tr><th>Адрес</th><th>кг</th><th>Заявки</th></tr></thead>"
            f"<tbody>{unassigned_rows}</tbody></table></div>"
        )

    violations_html = ""
    if violations:
        items = "".join(
            f"<li><code>{_e(v.get('type'))}</code> {_e(v.get('message'))}</li>" for v in violations[:40]
        )
        violations_html = f"<div class='warn'><strong>Нарушения ({len(violations)})</strong><ul>{items}</ul></div>"

    warnings_html = ""
    if warnings:
        items = "".join(f"<li>{_e(w)}</li>" for w in warnings[:20])
        warnings_html = f"<div class='warn'><strong>Предупреждения</strong><ul>{items}</ul></div>"

    payload = {
        "depot": depot,
        "routes": [
            {
                "number": route.get("route_number"),
                "vehicle": route.get("vehicle_id"),
                "color": PALETTE[i % len(PALETTE)],
                "geometry": (route.get("geometry") or {}).get("coordinates"),
                "stops": [
                    {
                        "seq": stop.get("seq"),
                        "address": stop.get("address"),
                        "arrive": stop.get("arrive"),
                        "demand": stop.get("demand_kg"),
                        "coords": stop.get("coords"),
                    }
                    for stop in route.get("stops", [])
                ],
            }
            for i, route in enumerate(routes)
        ],
    }

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>План доставки {_e(run.get('run_id'))}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>{STYLE}</style>
</head>
<body>
<header>
  <h1>План доставки — прогон {_e(run.get('run_id'))}</h1>
  <p>{_e(run.get('created_at'))} · расчёт {_e(run.get('elapsed_ms'))} мс ·
     матрица: {_e(options.get('matrix_provider'))} ·
     <a href="/api/runs/{_e(run.get('run_id'))}">JSON</a> ·
     <a href="/api/runs/{_e(run.get('run_id'))}/geojson">GeoJSON</a> ·
     <a href="/">на главную</a></p>
</header>
<main>
  {violations_html}
  {warnings_html}

  <div class="cards">
    <div class="card"><div class="label">Пробег после</div><div class="value">{_km(totals.get('distance_m'))}</div></div>
    <div class="card"><div class="label">Пробег «как в накладной»</div><div class="value">{_km(baseline.get('distance_m'))}</div></div>
    <div class="card good"><div class="label">Экономия</div><div class="value">{savings_value}</div>
      <div class="note">{_km(delta) if delta else ''}</div></div>
    <div class="card"><div class="label">Машин</div><div class="value">{_e(totals.get('vehicles_used'))}</div></div>
    <div class="card"><div class="label">Развезено</div><div class="value">{_e(totals.get('assigned_kg'))}</div>
      <div class="note">из {_e(totals.get('demand_kg'))} кг</div></div>
    <div class="card"><div class="label">Загрузка средняя</div>
      <div class="value">{round(float(totals.get('avg_load_ratio') or 0) * 100)} %</div></div>
  </div>

  <div class="panel">
    <h2>Карта маршрутов</h2>
    <div id="map"></div>
    <div class="legend" id="legend"></div>
    <p class="note">Клик по маршруту в легенде или в таблице подсвечивает его на карте.
      Карта грузится с CDN — без интернета останутся таблицы ниже.</p>
  </div>

  <div class="panel">
    <h2>Откуда берутся цифры</h2>
    <table>
      <tr><th>Что</th><th>Как считается</th></tr>
      <tr><td>Расстояния</td><td>матрица OSRM {_e(options.get('matrix_provider'))}, {_e(run.get('totals', {}).get('addresses_total'))} адресов + склад.
        Собрана {_e(options.get('matrix_built_at'))}</td></tr>
      <tr><td>Пробег маршрута</td><td>сумма ячеек матрицы по порядку объезда + возврат на базу.
        Геометрия на карте — тот же маршрут, посчитанный OSRM</td></tr>
      <tr><td>Время</td><td>время в пути из матрицы + погрузка {_e(options.get('loading_minutes'))} мин
        + разгрузка {_e(options.get('unload_minutes'))} мин на адрес + ожидание окон</td></tr>
      <tr><td>Смена</td><td>{_e(options.get('shift_start'))}–{_e(options.get('shift_end'))},
        работа не более {_e(options.get('max_workday_hours'))} ч, обед 13:00–14:00</td></tr>
      <tr><td>Мин. загрузка</td><td>{round(float(options.get('min_load_ratio') or 0) * 100)} % от грузоподъёмности</td></tr>
      <tr><td>Планировщик</td><td>{_e(run.get('solver'))}, водителей: {_e(options.get('drivers') or 'все')}</td></tr>
    </table>
  </div>

  <div class="panel">
    <h2>Маршруты ({len(routes)})</h2>
    <table>
      <thead><tr><th>#</th><th>Машина</th><th>Водитель</th><th>Загрузка</th>
        <th>Пробег</th><th>Работа</th><th>Возврат</th><th>Точек</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </div>

  {unassigned_html}
</main>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const data = {json.dumps(payload, ensure_ascii=False)};
const legend = document.getElementById('legend');
if (typeof L === 'undefined') {{
  document.getElementById('map').innerHTML =
    '<div class="err" style="margin:12px">Карта не загрузилась: нет интернета или CDN недоступен. ' +
    'Таблицы и цифры ниже/выше работают без неё.</div>';
}} else {{
  const map = L.map('map');
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
    {{ maxZoom: 19, attribution: 'OpenStreetMap' }}).addTo(map);
  const allBounds = [];
  const layers = {{}};

  if (data.depot && data.depot.lat != null) {{
    const depotMarker = L.circleMarker([data.depot.lat, data.depot.lon],
      {{ radius: 10, color: '#111827', weight: 3, fillColor: '#f59e0b', fillOpacity: 1 }}).addTo(map);
    depotMarker.bindTooltip('Склад', {{ permanent: false }});
    allBounds.push([data.depot.lat, data.depot.lon]);
  }}

  data.routes.forEach((route, index) => {{
    const group = L.layerGroup().addTo(map);
    if (route.geometry) {{
      const line = L.polyline(route.geometry.map(([lon, lat]) => [lat, lon]),
        {{ color: route.color, weight: 3, opacity: .85 }}).addTo(group);
      line.on('click', () => select(index));
      route.geometry.forEach(([lon, lat]) => allBounds.push([lat, lon]));
    }}
    route.stops.forEach(stop => {{
      if (!stop.coords) return;
      const marker = L.circleMarker([stop.coords.lat, stop.coords.lon],
        {{ radius: 4, color: route.color, weight: 2, fillColor: '#fff', fillOpacity: 1 }}).addTo(group);
      marker.bindTooltip(route.number + '. ' + stop.seq + '. ' + stop.address +
        '<br>прибытие ' + stop.arrive + ', ' + stop.demand + ' кг');
      allBounds.push([stop.coords.lat, stop.coords.lon]);
    }});
    layers[index] = group;

    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.dataset.index = index;
    chip.innerHTML = '<span class="dot" style="background:' + route.color + '"></span>' +
      route.number + ' · ' + route.vehicle;
    chip.onclick = () => select(index);
    legend.appendChild(chip);
  }});

  if (allBounds.length) map.fitBounds(allBounds, {{ padding: [20, 20] }});
  else map.setView([53.7, 23.83], 11);

  let selected = null;
  function select(index) {{
    selected = (selected === index) ? null : index;
    Object.entries(layers).forEach(([key, group]) => {{
      group.eachLayer(layer => {{
        if (layer.setStyle) {{
          const on = selected === null || Number(key) === selected;
          layer.setStyle({{ opacity: on ? .9 : .12, fillOpacity: on ? 1 : .15 }});
        }}
      }});
    }});
    document.querySelectorAll('.chip').forEach(chip => {{
      chip.classList.toggle('active', Number(chip.dataset.index) === selected);
    }});
  }}
  document.querySelectorAll('tr[data-route]').forEach(row => {{
    row.style.cursor = 'pointer';
    row.onclick = (event) => {{
      if (event.target.closest('summary')) return;
      select(Number(row.dataset.route));
    }};
  }});
}}
</script>
</body>
</html>"""


def render_index(runs: list[dict], health: dict) -> str:
    rows = "".join(
        f"<tr><td><a href='/api/runs/{_e(run.get('run_id'))}/report'>{_e(run.get('run_id'))}</a></td>"
        f"<td>{_e(run.get('created_at'))}</td>"
        f"<td>{_km((run.get('totals') or {}).get('distance_m'))}</td>"
        f"<td>{_e((run.get('totals') or {}).get('vehicles_used'))}</td>"
        f"<td>{_e((run.get('totals') or {}).get('assigned_kg'))} / "
        f"{_e((run.get('totals') or {}).get('demand_kg'))} кг</td></tr>"
        for run in runs
    ) or "<tr><td colspan='5' class='note'>Пока нет прогонов — нажми «Рассчитать»</td></tr>"

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MilkLogistic — проверка расчётов</title>
<style>{STYLE}</style>
</head>
<body>
<header>
  <h1>MilkLogistic — проверка расчётов</h1>
  <p>{_e(datetime.now().strftime('%d.%m.%Y %H:%M'))} · это страница бэкенда, не фронтенд</p>
</header>
<main>
  <div class="panel">
    <h2>Состояние</h2>
    <table>
      <tr><th>API</th><td>{_e(health.get('status'))}</td></tr>
      <tr><th>Заявок / адресов</th><td>{_e(health.get('points'))} / {_e(health.get('addresses'))}</td></tr>
      <tr><th>Машин</th><td>{_e(health.get('vehicles'))}</td></tr>
      <tr><th>Груз</th><td>{_e(health.get('demand_kg'))} кг</td></tr>
      <tr><th>Матрица</th><td>{_e((health.get('matrix') or {}).get('size'))} × {_e((health.get('matrix') or {}).get('size'))},
        провайдер {_e((health.get('matrix') or {}).get('provider'))}</td></tr>
    </table>
  </div>

  <div class="panel">
    <h2>Рассчитать план</h2>
    <div class="toolbar">
      <label>Водителей: <input id="drivers" type="number" min="1" max="100" value="16"></label>
      <button id="run">Рассчитать и открыть отчёт</button>
      <span id="status" class="note"></span>
    </div>
    <p class="note">Пустое поле — все водители. Расчёт занимает около секунды,
      геометрия маршрутов подтягивается из сервиса маршрутизации.</p>
  </div>

  <div class="panel">
    <h2>Последние прогоны</h2>
    <table>
      <thead><tr><th>Прогон</th><th>Когда</th><th>Пробег</th><th>Машин</th><th>Развезено</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>

  <div class="panel">
    <h2>Что где смотреть</h2>
    <table>
      <tr><th><a href="/docs">/docs</a></th><td>Swagger: все эндпоинты, можно дёргать вручную</td></tr>
      <tr><th><a href="/api/health">/api/health</a></th><td>состояние: точки, машины, матрица</td></tr>
      <tr><th><a href="/api/data">/api/data</a></th><td>данные, которые реально загрузились</td></tr>
      <tr><th>POST /api/optimize</th><td>расчёт плана, возвращает JSON и run_id</td></tr>
      <tr><th>/api/runs/&lt;id&gt;/report</th><td>этот отчёт: карта, маршруты, расшифровка</td></tr>
      <tr><th>/api/runs/&lt;id&gt;/geojson</th><td>линии маршрутов для любой ГИС</td></tr>
    </table>
  </div>
</main>

<script>
const button = document.getElementById('run');
const status = document.getElementById('status');
button.onclick = async () => {{
  button.disabled = true;
  status.textContent = 'Считаю...';
  try {{
    const raw = document.getElementById('drivers').value.trim();
    const body = raw ? {{ drivers: Number(raw) }} : {{}};
    const response = await fetch('/api/optimize', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(body)
    }});
    const data = await response.json();
    if (!response.ok) {{
      status.textContent = 'Ошибка: ' + (data.detail || JSON.stringify(data));
      button.disabled = false;
      return;
    }}
    location.href = '/api/runs/' + data.run_id + '/report';
  }} catch (error) {{
    status.textContent = 'Ошибка сети: ' + error;
    button.disabled = false;
  }}
}};
</script>
</body>
</html>"""
