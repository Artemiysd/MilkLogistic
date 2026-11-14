import { useEffect } from "react";
import { MapContainer, TileLayer, Marker, Popup, CircleMarker, Polyline, Tooltip, useMap } from "react-leaflet";
import type { DeliveryPoint } from "../types/logistics";
import L from "leaflet";
import depotIconImage from "../assets/assetslogo.png";


const depotIcon = L.icon({
    iconUrl: depotIconImage,
    iconSize: [45, 45],
    iconAnchor: [22, 45],
    popupAnchor: [0, -45],

});
export interface MapRoute {
    number: number;
    color: string;
    /** [lat, lon] для react-leaflet */
    positions: [number, number][];
}

function RemoveLeafletPrefix() {
    const map = useMap();
    map.attributionControl.setPrefix(false);
    return null;
}

function FitBounds({
                       points,
                       routes,
                       depot,
                   }: {
    points: DeliveryPoint[];
    routes: MapRoute[];
    depot: Depot;
}) {
    const map = useMap();
    useEffect(() => {
        const coordinates: [number, number][] = [
            [depot.latitude, depot.longitude],
        ];
        routes.forEach((route) => {
            coordinates.push(...route.positions);
        });
        points.forEach((point) => {
            coordinates.push([point.latitude, point.longitude]);
        });
        if (coordinates.length > 0) {
            map.fitBounds(coordinates, { padding: [30, 30] });
        }
    }, [map, points, routes, depot]);
    return null;
}

interface LogisticsMapProps {
    points: DeliveryPoint[];
    depot: Depot;
    routes?: MapRoute[];
    selectedRoute?: number | null;
    showPoints?: boolean;
}
interface Depot {
    latitude: number;
    longitude: number;
}

function LogisticsMap({ points,
                          depot,
                          routes = [],
                          selectedRoute = null,
                          showPoints = true,}: LogisticsMapProps) {
    const position: [number, number] = [depot.latitude, depot.longitude];
    const visibleRoutes = selectedRoute === null
        ? routes
        : routes.filter((route) => route.number === selectedRoute);
    return (
        <MapContainer center={position} zoom={12} style={{ height: "500px", width: "100%" }}>
            <RemoveLeafletPrefix />
            <FitBounds
                points={points}
                routes={visibleRoutes}
                depot={depot}
            />
            <TileLayer
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                attribution="@ OpenStreetMap contributors"
            />
            <Marker position={position} icon={depotIcon}>
                <Popup><strong>Главный склад</strong> </Popup> </Marker>

            {visibleRoutes.map((route) => (
                <Polyline
                    key={`route-${route.number}`}
                    positions={route.positions}
                    pathOptions={{
                        color: route.color,
                        weight: 5,
                        opacity: 1,
                    }}
                >
                    <Tooltip sticky>Маршрут {route.number}</Tooltip>
                </Polyline>
            ))}

            {showPoints && points.map((point) => (
                <CircleMarker
                    key={point.id}
                    center={[point.latitude, point.longitude]}
                    radius={4}
                >
                    <Popup>
                        <strong>{point.name}</strong>
                        <br />
                        {point.address}
                        <br />
                        Заказ: {point.demand} кг
                    </Popup>
                </CircleMarker>
            ))}
        </MapContainer>
    );
}
export default LogisticsMap;
