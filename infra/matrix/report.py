"""
Отчёт по готовой матрице: что именно лежит в data/matrix_cache.json.

    py infra/matrix/report.py
    py infra/matrix/report.py --top 10

Показывает то, что важно алгоритму и жюри:
  * размер и происхождение матрицы;
  * реальные расстояния и время по дорогам;
  * точки с одинаковыми координатами (один адрес — несколько организаций);
  * асимметрию (односторонние улицы) — solver обязан её учитывать;
  * самые длинные плечи.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE = ROOT / "data" / "matrix_cache.json"


def km(value_m: float) -> str:
    return f"{value_m / 1000:.1f} км"


def minutes(value_s: float) -> str:
    return f"{value_s / 60:.0f} мин"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    if not args.cache.exists():
        print(f"Нет файла {args.cache}. Сначала: py infra/matrix/build_matrix.py")
        return 1

    cache = json.loads(args.cache.read_text(encoding="utf-8"))
    points = cache["points"]
    dist = cache["distance_m"]
    dur = cache["duration_s"]
    n = cache["size"]

    print(f"Файл:        {args.cache} ({args.cache.stat().st_size / 1e6:.2f} МБ)")
    print(f"Провайдер:   {cache['provider']} {cache.get('base_url') or ''}")
    print(f"Собрано:     {cache['built_at']}")
    print(f"Размер:      {n} x {n} ({n * n:,} ячеек), блоков-запросов: {cache['stats']['requests']}")
    print(f"Точек:       {sum(1 for p in points if p['kind'] == 'delivery')} + склад")
    print()

    pairs = [
        (dist[i][j], dur[i][j], i, j)
        for i in range(n)
        for j in range(n)
        if i != j
    ]
    pairs.sort()
    distances = sorted(p[0] for p in pairs)
    durations = sorted(p[1] for p in pairs)

    def pct(values: list[float], q: float) -> float:
        idx = min(len(values) - 1, int(len(values) * q))
        return values[idx]

    print("Расстояния по дорогам (все пары точка-точка):")
    print(
        f"  мин {km(distances[0])} | медиана {km(pct(distances, 0.5))} | "
        f"90% {km(pct(distances, 0.9))} | макс {km(distances[-1])}"
    )
    print(
        f"  время в пути: медиана {minutes(pct(durations, 0.5))}, "
        f"90% {minutes(pct(durations, 0.9))}, макс {minutes(durations[-1])}"
    )
    print()

    zero = {i for d, _, i, j in pairs if d == 0} | {j for d, _, i, j in pairs if d == 0}
    if zero:
        groups: dict[tuple[float, float], list[int]] = {}
        for i in zero:
            key = (round(points[i]["lat"], 5), round(points[i]["lon"], 5))
            groups.setdefault(key, []).append(i)
        print(f"Совпадающие координаты: {len(groups)} адресов, где стоят несколько точек")
        print("  (алгоритму это выгодно: один подъезд, но выгрузка на каждую организацию своя)")
        for idxs in list(groups.values())[:5]:
            address = points[idxs[0]]["address"]
            ids = ", ".join(points[i]["id"] for i in idxs)
            print(f"  {address}: {len(idxs)} точки — {ids}")
        print()

    rel = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = dist[i][j], dist[j][i]
            if a and b:
                rel.append(abs(a - b) / max(a, b))
    rel.sort()
    print("Асимметрия (односторонние улицы, развязки):")
    print(
        f"  медиана {pct(rel, 0.5) * 100:.1f}% | 95% {pct(rel, 0.95) * 100:.1f}% | "
        f"макс {rel[-1] * 100:.1f}% | пар с разницей >20%: {sum(1 for r in rel if r > 0.2)}"
    )
    print("  вывод: матрица несимметрична, VRP-солвер должен работать с направленными рёбрами")
    print()

    print(f"Самые длинные плечи (топ-{args.top}):")
    for d, t, i, j in pairs[-args.top :][::-1]:
        print(
            f"  {km(d):>8} / {minutes(t):>7}  {points[i]['address']}  ->  {points[j]['address']}"
        )
    print()

    depot_to_points = sorted(dist[0][j] for j in range(1, n))
    print("От склада (Гаспадарчая 28):")
    print(
        f"  медиана {km(pct(depot_to_points, 0.5))} | макс {km(depot_to_points[-1])} | "
        f"суммарно по всем точкам {km(sum(depot_to_points))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
