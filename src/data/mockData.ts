import data from "./logisticsData.json";
export const deliveryPoints = data.delivery_points
    .filter((point) => point.coords !== null)
    .map((point) => ({
    id: point.id,
    name: point.name,
    address: point.address,
    demand:point.demand,
    latitude: point.coords!.lat,
    longitude: point.coords!.lon,
}));
export const depot = {
    latitude: data.depot.coords.lat,
    longitude: data.depot.coords.lon,
}
export const vehicles = data.vehicles;