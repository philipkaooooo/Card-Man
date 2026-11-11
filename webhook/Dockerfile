# Plain container instead of buildpacks: nothing to detect, no dependencies to install.
FROM node:22-slim
WORKDIR /app
COPY package.json server.js ./
COPY lib ./lib
ENV NODE_ENV=production
USER node
EXPOSE 8080
CMD ["node", "server.js"]
