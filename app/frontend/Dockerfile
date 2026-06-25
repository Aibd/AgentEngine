# syntax=docker/dockerfile:1
#
# Frontend: build the Vite SPA, then serve the static bundle with nginx.
# nginx also reverse-proxies /api to the backend service (same origin → no CORS).

# --- build stage -------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci || npm install
COPY . .
RUN npm run build          # tsc -b && vite build → /app/dist

# --- runtime stage -----------------------------------------------------------
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
