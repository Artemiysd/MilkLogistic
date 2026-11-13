import { useState} from "react";
import { deliveryPoints, vehicles,depot } from "../data/mockData.ts";
import "./Dashboard.css";
import RouteInfo from "../components/RouteInfo";
import LogisticsMap from "../components/LogisticsMap";
function Dashboard() {
    const totalDemand = deliveryPoints.reduce(
        (sum, point) => sum + point.demand,
        0
    );
    const [isOptimized, setIsOptimized] = useState(false);
    const [mapMode, setMapMode] = useState<"points" | "routes">("points");
    const totalVehicles = vehicles.reduce(
        (sum,vehicle) => sum+ vehicle.count,0
    );
    return (
        <main className="dashboard">
            <header className="header">
                <div>
                    <h1> Dairy Logistics</h1>
                    <p>Оптимизация доставки молочной продукции</p>
                </div>
            </header>
            <div className="dashboard-content">
                <aside className="sidebar">
                    <h2>Общая информация</h2>
                    <div className="info-card">
                        <div>
                            <strong>{deliveryPoints.length}</strong>
                            <p>Точек доставки</p>
                        </div>
                    </div>
                    <div className="info-card">
                        <div>
                            <strong>{totalVehicles}</strong>
                            <p>Транспорта</p>
                        </div>
                    </div>
                    <div className="info-card">
                        <strong>{totalDemand} кг</strong>
                        <p>Общий обьем</p>
                    </div>
                    <button
                        className="optimize-button"
                        onClick={() => setIsOptimized(true)}
                    >
                        Оптимизировать маршрут
                    </button>
                </aside>
                <section className ="map-container">
                    <div className="map-controls">
                        <button
                            className={mapMode === "points" ? "active" : ""}
                            onClick={() => setMapMode("points")}
                        >
                            Все точки
                        </button>
                        <button
                            className={mapMode === "routes" ? "active" : ""}
                            onClick={() => setMapMode("routes")}
                        >
                            Маршруты
                        </button>
                    </div>
                    {mapMode === "points" ? (
                        <LogisticsMap points={deliveryPoints} depot={depot} />
                    ) : (
                        <div className="routes-placeholder">
                            <h3>Маршруты ещё не построены</h3>
                            <p>Ожидаем результат алгоритма оптимизации</p>
                        </div>
                    )}

                </section>
            </div>
            <section className="results">
                <h2>Результат оптимизации</h2>
                <RouteInfo
                    vehicle="Грузовик 5т"
                    points={ 12}
                    load ={ 4320}
                    capacity={ 5000}
                    distance={ 38}
                    time="1ч 15 мин" />
                <div className="results-grid">
                    <div className = "result-card">
                        <p>До оптимизации</p>
                        <strong> 145км</strong>
                    </div>
                    <div className = "result-card">
                        <p>После оптимизации</p>
                        <strong>{isOptimized ? "112км" : "-"}</strong>
                    </div>
                    <div className = "result-card">
                        <p>Экономия</p>
                        <strong>{isOptimized ? "23%" : "-"}</strong>
                    </div>
                </div>
            </section>
        </main>
    );
}
export default Dashboard;