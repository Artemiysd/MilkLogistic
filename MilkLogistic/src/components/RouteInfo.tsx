interface RouteInfoProps{
    vehicle:string;
    points:number;
    load:number;
    capacity:number;
    distance:number;
    time:string;
}
function RouteInfo({
    vehicle,
    points,
    load,
    capacity,
    distance,
    time,
                   }:RouteInfoProps){
    return(
        <div className="route-info">
            <h3>Информация о маршруте</h3>
            <div className="route-info-grid">
                <div className="route-info-item">
                    <span>Транспорт: </span>
                    <strong>{vehicle}</strong>
                </div>
                <div className="route-info-item">
                    <span>Точек: </span>
                    <strong>{points}</strong>
                </div>
                <div className="route-info-item">
                    <span>Загрузка </span>
                    <strong>{load} / {capacity} кг</strong>
                </div>
                <div className="route-info-item">
                    <span>Расстояние </span>
                    <strong>{distance} км</strong>
                </div>
                <div className="route-info-item">
                    <span>Время: </span>
                    <strong>{time}</strong>
                </div>
            </div>
        </div>
    );
}
export default RouteInfo;