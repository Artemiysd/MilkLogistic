# Сборка фронтенда (Vite) и раздача через nginx с проксированием /api.
# Контекст сборки — корень репозитория, поэтому ожидается обычная раскладка:
#   index.html, src/, package.json, vite.config.ts
# Если фронтенд лежит в подпапке frontend/ — поменяйте context в docker-compose.yml.

FROM node:22-alpine AS build
WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

ENV API_UPSTREAM=api:8000 \
    NGINX_ENVSUBST_FILTER=API_UPSTREAM

COPY --from=build /app/dist /usr/share/nginx/html
COPY infra/nginx/templates /etc/nginx/templates

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
