import type {DeliveryPoint, Vehicle} from "../types/logistics.ts";
export const deliveryPoints: DeliveryPoint[] = [
    {
        id: "point-1",
        name:"Магазин №1",
        address: "Ул.Ленина,10",
        demand: 1200,
        latitude:53.671,
        longitude:23.814
    },
    {
        id: "point-2",
        name:"Магазин №2",
        address: "ул.Советская,25",
        demand: 800,
        latitude:53.678,
        longitude:23.900
    },
    {
        id: "point-3",
        name:"Магазин №3",
        address: "Ул.Победы,15",
        demand: 1500,
        latitude:53.680,
        longitude:23.700
    },
    {
        id: "point-5",
        name:"Магазин №5",
        address: "Ул.Лиможа,25",
        demand: 1200,
        latitude:53.900,
        longitude:23.800
    }

];
export const vehicles: Vehicle[] = [
    {
        id: "truck-1",
        name:"машина 1",
        capacity: 5000
    },
    {
        id: "truck-2",
        name:"машина 2",
        capacity: 1500
    },
    {
        id: "truck-3",
        name:"машина 3",
        capacity: 4500
    },
]