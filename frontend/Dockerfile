# ─── Stage 1: Build ───────────────────────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# VITE_API_URL is baked into the JS bundle at build time by Vite.
# Pass it as a build arg in docker-compose (build.args) or via:
#   docker build --build-arg VITE_API_URL=https://api.example.com .
# Falls back to /api if not set (nginx proxy handles routing in that case).
ARG VITE_API_URL=/api
ENV VITE_API_URL=$VITE_API_URL

# VITE_PREVIEW_MODE=true builds a landing-only bundle: the SPA blocks
# every route except /, and the navbar / hero CTAs swap their auth
# controls for a "View on GitHub" link. Useful for hosting the marketing
# site without exposing the trading portal. Default empty = full portal.
ARG VITE_PREVIEW_MODE=
ENV VITE_PREVIEW_MODE=$VITE_PREVIEW_MODE

RUN npm run build

# ─── Stage 2: Serve ───────────────────────────────────────────────────────────
FROM nginx:1.31-alpine

# Remove default config
RUN rm /etc/nginx/conf.d/default.conf

# nginx template — /etc/nginx/templates/*.template files are processed with
# envsubst at container start, output written to /etc/nginx/conf.d/.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# nginx entrypoint handles envsubst + nginx -g "daemon off;"
