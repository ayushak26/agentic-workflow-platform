FROM node:24.5.0-alpine3.22 AS builder

WORKDIR /ui
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_URL=
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

FROM nginx:1.29.0-alpine3.22
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /ui/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "--spider", "http://127.0.0.1:8080/"]
