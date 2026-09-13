# CanopyLens web client
# ---------------------------------------------------------------------------
# Build context is `frontend/`:
#
#   docker build -f frontend/Dockerfile -t canopylens-web frontend
#
# VITE_* values are compiled into the bundle at build time, so the API base URL
# is a build argument rather than a runtime environment variable.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Empty by default: nginx below proxies /api to the backend, so the bundle can
# use same-origin relative URLs. Override when the API lives on another host:
#   docker build --build-arg VITE_API_BASE_URL=https://api.example.com ...
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.27-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
