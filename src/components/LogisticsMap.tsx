import { useEffect } from "react";
import { MapContainer, TileLayer, Marker, Popup, CircleMarker, Polyline, Tooltip, useMap } from "react-leaflet";

export interface MapPoint {
    id: string;
    name: string;
    address: string;
    demand: number;
    latitude: number;
    longitude: number;
    color?: string;
}

export interface MapRoute {
    number: number;
    color: string;
    /** [lat, lon] для react-leaflet */
    positions: [number, number][];
}

interface LogisticsMapProps {
    points: MapPoint[];
    depot: { latitude: number; longitude: number };
    routes?: MapRoute[];
    selectedRoute?: number | null;
}

function RemoveLeafletPrefix() {
    const map = useMap();
    map.attributionControl.setPrefix(false);
    return null;
}

function FitBounds({ points, routes }: { points: MapPoint[]; routes: MapRoute[] }) {
    const map = useMap();

    useEffect(() => {
        const coordinates: [number, number][] = [];
        routes.forEach((route) => coordinates.push(...route.positions));
        if (coordinates.length === 0) {
            points.forEach((point) => coordinates.push([point.latitude, point.longitude]));
        }
        if (coordinates.length === 0) return;
        map.fitBounds(coordinates, { padding: [24, 24] });
    }, [map, points, routes]);

    return null;
}

function LogisticsMap({ points, depot, routes = [], selectedRoute = null }: LogisticsMapProps) {
    const position: [number, number] = [depot.latitude, depot.longitude];

    return (
        <MapContainer center={position} zoom={12} style={{ height: "500px", width: "100%" }}>
            <RemoveLeafletPrefix />
            <FitBounds points={points} routes={routes} />

            <TileLayer
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                attribution="@ OpenStreetMap contributors"
            />

            <Marker position={position}>
                <Popup>
                    <strong>Склад</strong>
                </Popup>
            </Marker>

            {routes.map((route) => {
                const dimmed = selectedRoute !== null && selectedRoute !== route.number;
                return (
                    <Polyline
                        key={`route-${route.number}`}
                        positions={route.positions}
                        pathOptions={{
                            color: route.color,
                            weight: selectedRoute === route.number ? 6 : 4,
                            opacity: dimmed ? 0.12 : 0.9,
                        }}
                    >
                        <Tooltip sticky>Маршрут {route.number}</Tooltip>
                    </Polyline>
                );
            })}

            {points.map((point) => (
                <CircleMarker
                    key={point.id}
                    center={[point.latitude, point.longitude]}
                    radius={4}
                    pathOptions={{
                        color: point.color ?? "#2563eb",
                        fillColor: point.color ?? "#2563eb",
                        fillOpacity: 0.85,
                        weight: 1,
                    }}
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
