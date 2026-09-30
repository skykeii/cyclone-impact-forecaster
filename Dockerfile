FROM node:20-alpine AS frontend-builder
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
# We need to configure Next.js to do a static export
RUN echo '/** @type {import("next").NextConfig} */\nconst nextConfig = { output: "export" };\nexport default nextConfig;' > next.config.mjs
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

# System dependencies for geospatial libs
RUN apt-get update && apt-get install -y gdal-bin libgdal-dev gcc g++ && rm -rf /var/lib/apt/lists/*
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

# Backend dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install fastapi uvicorn google-genai

# Copy backend app & data & scripts
COPY backend /app/backend
COPY data /app/data
COPY cache /app/cache
COPY scripts /app/scripts
# Copy frontend static build to a folder the backend can serve
COPY --from=frontend-builder /app/out /app/frontend_static

EXPOSE 8080

# In main.py, FastAPI will need a Mount for the static files, but since I can't guarantee that's edited, 
# I will supply a wrapper script or edit main.py to mount the static folder.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
