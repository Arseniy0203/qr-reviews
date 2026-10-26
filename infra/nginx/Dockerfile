# Production nginx: builds the dashboard and serves it next to the API proxy.
# Build context: repository root.

FROM node:24-alpine AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.28-alpine
COPY infra/nginx/templates/ /etc/nginx/templates/
COPY --from=frontend /app/dist /usr/share/nginx/html
# Reload periodically so renewed Let's Encrypt certificates are picked up.
CMD ["/bin/sh", "-c", "while :; do sleep 6h & wait $!; nginx -s reload; done & exec nginx -g 'daemon off;'"]
