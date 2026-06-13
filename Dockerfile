# Dashboard + tooling image (also used as a one-shot to render mediamtx.yml).
FROM node:20-alpine
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY lib ./lib
COPY bin ./bin
COPY dashboard ./dashboard

# devices.yml (registry) and /runtime (generated config) are provided via bind mounts at runtime.
EXPOSE 8080
CMD ["node", "dashboard/server.js"]
