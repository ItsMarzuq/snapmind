FROM node:22-alpine

WORKDIR /code

COPY package*.json ./
RUN npm install

COPY . .

# Next.js resolves rewrites during the production build.
ENV API_INTERNAL_URL=http://api:8000

RUN npm run build

CMD ["npm", "start"]