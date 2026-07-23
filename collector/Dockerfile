FROM python:3.12-slim
WORKDIR /app
COPY collector/requirements.txt ./collector/requirements.txt
RUN pip install --no-cache-dir -r collector/requirements.txt
COPY registry.json ./registry.json
COPY collector ./collector
# Baked seed for flows/prices so the HTTP publisher can serve all three JSONs on
# a fresh volume (state.json is written live). The volume mounts over /app/data,
# so the seed lives in /app/seed and is copied into the volume at startup if missing.
COPY data ./seed
# state.json + served JSON live on the mounted volume in production.
VOLUME ["/app/data"]
EXPOSE 8080
CMD ["python", "-m", "collector.collector", "--live"]
