# Discord Chaos Bot — container image (works on amd64 and arm64)
FROM python:3.12-slim

# ffmpeg is required for voice playback
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code. The token (.env) and sounds/ are provided at runtime — see
# docker-compose.yml — so they are NOT baked into the image.
COPY bot.py config.py responses.py ./
COPY cogs ./cogs

# Inside the container ffmpeg lives on PATH; this is the sane default.
ENV FFMPEG_PATH=ffmpeg

CMD ["python", "bot.py"]
