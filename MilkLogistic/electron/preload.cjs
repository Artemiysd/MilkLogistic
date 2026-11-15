/**
 * Мост в страницу: сообщает фронтенду адрес API.
 * Настройка задаётся в main.cjs (env MILK_API_BASE или milk-config.json).
 */
const { contextBridge } = require("electron");

const prefix = "--milk-api-base=";
const argument = process.argv.find((item) => item.startsWith(prefix));
const apiBase = argument ? argument.slice(prefix.length) : "";

contextBridge.exposeInMainWorld("milkApiBase", apiBase);
