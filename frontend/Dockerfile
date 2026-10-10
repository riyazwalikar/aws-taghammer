FROM node:20-slim

WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

# Listens on $APP_PORT (default 8080; see vite.config.ts). EXPOSE is documentation
# only — compose maps the real APP_PORT. `vite preview` proxies /api -> backend:8000.
EXPOSE 8080
CMD ["npm", "run", "preview"]
