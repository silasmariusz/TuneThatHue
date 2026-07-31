# TuneThatHue in a container.
#
# Run it with --network host. That is not laziness: every way this box is found on the
# network - mDNS for Sendspin, SSDP for DLNA, a UDP broadcast for Squeezebox - relies on
# multicast and broadcast reaching the LAN, and none of that survives a bridge network.
#
#   docker run -d --name tunethathue --network host \
#     -v tunethathue:/config \
#     ghcr.io/silasmariusz/tunethathue:latest
#
# The panel is then on http://<host>:8080.

FROM python:3.14-slim-bookworm

LABEL org.opencontainers.image.title="TuneThatHue" \
      org.opencontainers.image.description="DJ-style VFX daemon for Philips Hue" \
      org.opencontainers.image.source="https://github.com/silasmariusz/TuneThatHue" \
      org.opencontainers.image.licenses="Apache-2.0"

# ffmpeg is the decoder. Debian's is LGPL-built and carries every codec these protocols
# can deliver, so there is no reason to bundle our own here.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-docker.txt ./
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY python/ ./python/
COPY effects/ ./effects/
COPY resources/ ./resources/
COPY config/ ./config/
COPY install/ ./install/

# Settings and the paired bridge credentials live on a volume, so an upgrade is a new
# image and nothing else.
VOLUME ["/config"]
ENV TTH_CONFIG=/config/hue-box.toml

EXPOSE 8080/tcp 6980/udp 8928/tcp 8930/tcp 1900/udp 3483/udp

# Answering on the panel is the only honest test of "up": the process can be alive while
# the daemon is stuck.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/status',timeout=3)" || exit 1

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
