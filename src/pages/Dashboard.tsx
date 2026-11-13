import { useState} from "react";
import { deliveryPoints, vehicles,depot } from "../data/mockData.ts";
import "./Dashboard.css";
import LogisticsMap from "../components/LogisticsMap";
function Dashboard() {
    const totalDemand = deliveryPoints.reduce(
        (sum, point) => sum + point.demand,
        0
    );
    const [isOptimized, setIsOptimized] = useState(false);
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
                   <LogisticsMap points={deliveryPoints} depot ={depot} />
                </section>
            </div>
            <section className="results">
                <h2>Результат оптимизации</h2>
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