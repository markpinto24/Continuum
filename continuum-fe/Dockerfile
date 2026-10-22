# The production web front door: the built UI, served by Caddy, which also
# proxies /api to the backend. One origin for both is not optional — the session
# cookie is SameSite=Strict and cookie writes need the CSRF header, so sign-in
# only works when the page and the API share a host (CLAUDE.md, "Setting
# VITE_API_URL to call the API cross-origin"). Caddy also gets the HTTPS
# certificate for the domain by itself.
#
# Development does not use this: `yarn dev` runs the Vite server and its proxy.

# ---------------------------------------------------------------------------
# Stage 1 — build the static bundle
# ---------------------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

# yarn 1, pinned by packageManager in package.json; corepack provides it.
RUN corepack enable

# Dependencies first: reused whenever the lockfile is unchanged.
COPY package.json yarn.lock ./
RUN yarn install --frozen-lockfile --non-interactive

COPY . .
# No VITE_API_URL: the bundle calls /api on its own origin, which Caddy proxies.
RUN yarn build


# ---------------------------------------------------------------------------
# Stage 2 — serve it
# ---------------------------------------------------------------------------
FROM caddy:2.8-alpine

COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv

EXPOSE 80 443
