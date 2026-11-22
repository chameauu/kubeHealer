FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

FROM nginx:alpine

COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html

# `nginx -t` cannot run here: the resolver is an in-cluster name, and nginx
# resolves it while loading the configuration.

# nginx:alpine ships the nginx user as uid 101. Use the numeric ids so the
# Kubernetes runAsNonRoot check can verify them, and keep the web root read-only.
RUN chown -R 101:101 /usr/share/nginx/html \
    && chmod -R a-w /usr/share/nginx/html

USER 101:101

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1

ENTRYPOINT ["nginx", "-g", "daemon off;"]
