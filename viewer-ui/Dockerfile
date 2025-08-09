FROM node:20-alpine
WORKDIR /app
# curl for healthcheck if needed
RUN apk add --no-cache curl
COPY package.json ./
RUN npm install && npm install -D @vitejs/plugin-react
COPY . .
CMD ["npm", "run", "dev", "--", "--host"]
