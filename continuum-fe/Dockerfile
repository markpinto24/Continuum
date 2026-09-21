# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Stage 1 — build the static bundle
# ---------------------------------------------------------------------------
FROM node:22-alpine AS builder

WORKDIR /app

# Lockfile first, in its own layer: dependencies change far less often than the
# app does, so this keeps rebuilds to a few seconds.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2 — serve it
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q --spider http://localhost/ || exit 1
