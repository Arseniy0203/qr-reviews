# Dev image (Vite dev server). The production build is produced in infra/nginx/Dockerfile.
FROM node:24-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]
