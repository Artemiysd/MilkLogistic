import {MapContainer,TileLayer,Marker,Popup,CircleMarker,useMap} from "react-leaflet";
import type { DeliveryPoint} from "../types/logistics"

function RemoveLeafletPrefix(){
    const map= useMap();
    map.attributionControl.setPrefix(false);
    return null;
}
interface LogisticsMapProps {
    points: DeliveryPoint[];
    depot: Depot;
}
interface Depot{
    latitude: number;
    longitude: number;
}
function LogisticsMap({points,depot}: LogisticsMapProps){
    const position: [number, number] =[depot.latitude, depot.longitude,];
    return (
        <MapContainer
            center={position}
            zoom={12}
            style={{height: "100%", width: "100%"}}
        >
            <RemoveLeafletPrefix />

            <TileLayer
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                attribution="@ OpenStreetMap contributors"
            />
            <Marker position={position}><Popup>Главный склад</Popup> </Marker>
            {points.map((point) => (
                <CircleMarker
                    key={point.id}
                    center={[point.latitude,point.longitude]}
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