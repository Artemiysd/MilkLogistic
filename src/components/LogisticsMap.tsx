import {MapContainer,TileLayer,Marker,Popup,useMap} from "react-leaflet";
import type { DeliveryPoint} from "../types/logistics"

function RemoveLeafletPrefix(){
    const map= useMap();
    map.attributionControl.setPrefix(false);
    return null;
}
interface LogisticsMapProps {
    points: DeliveryPoint[];
}
function LogisticsMap({points}: LogisticsMapProps){
    const position: [number, number] = [53.6694,23.8131];
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
                <Marker
                    key={point.id}
                    position={[point.latitude,point.longitude]}
                >
                    <Popup>
                        <strong>{point.name}</strong>
                        <br />
                        {point.address}
                        <br />
                        Заказ: {point.demand} кг
                    </Popup>
                </Marker>
                ))}
        </MapContainer>
        );
}
export default LogisticsMap;