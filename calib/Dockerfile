# PiCrawler web calibration tool. aarch64 (Raspberry Pi).
FROM python:3.11-slim-bookworm

# Runtime libs: SDL2 for pygame (robot_hat.music imports it), portaudio for
# pyaudio if pulled in, i2c-tools for debugging. build-essential/python3-dev to
# compile spidev/lgpio wheels if needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git build-essential python3-dev i2c-tools \
      libsdl2-2.0-0 libsdl2-mixer-2.0-0 libsdl2-image-2.0-0 libsdl2-ttf-2.0-0 \
      libportaudio2 portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

ENV GPIOZERO_PIN_FACTORY=lgpio

RUN pip install --no-cache-dir \
      flask smbus2 gpiozero lgpio spidev pillow "pygame>=2.1.2"

# SunFounder libs (same sources as the host install)
RUN pip install --no-cache-dir "git+https://github.com/sunfounder/robot-hat.git@2.5.x"
RUN pip install --no-cache-dir "git+https://github.com/sunfounder/picrawler.git"

WORKDIR /app
COPY app.py .
COPY templates ./templates

EXPOSE 8080
CMD ["python", "app.py"]
