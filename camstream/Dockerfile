# picamera2 is NOT in vanilla Debian — it lives in archive.raspberrypi.com.
# That archive's raw signing key is self-signed with SHA1, which Trixie's sqv
# verifier rejects outright ("SHA1 is not considered secure since 2026-02-01"),
# so downloading the key fails. Debian's packaged raspberrypi-archive-keyring
# validates fine, so the build copies the host's copy in — which also
# guarantees the container trusts exactly what the host trusts.
#
# scripts/build_camstream.sh stages the keyring next to this Dockerfile.
FROM debian:trixie-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY raspberrypi-archive-keyring.pgp /usr/share/keyrings/rpi.pgp

# Pull the camera stack from the Pi archive so libcamera matches the host's
# (Debian ships 0.4.0; the host runs 0.7.1+rpt and drives the same kernel).
RUN echo "deb [signed-by=/usr/share/keyrings/rpi.pgp] \
http://archive.raspberrypi.com/debian/ trixie main" \
        > /etc/apt/sources.list.d/raspi.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3-picamera2 \
        python3-flask \
        python3-numpy \
        python3-opencv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py ./
COPY templates/ ./templates/

EXPOSE 8080
CMD ["python3", "app.py"]
