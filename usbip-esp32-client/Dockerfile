FROM ghcr.io/home-assistant/base:latest

# Install system dependencies
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-flask \
    linux-tools-usbip \
    kmod \
    && rm -rf /var/cache/apk/*

# Copy root filesystem (s6-overlay scripts)
COPY rootfs /

# Remove any __pycache__ that slipped in and set permissions
RUN find /etc/cont-init.d /etc/cont-finish.d /etc/services.d -name '__pycache__' -exec rm -rf {} + 2>/dev/null; \
    chmod +x /etc/cont-init.d/*.py \
    && chmod +x /etc/cont-finish.d/*.py \
    && chmod +x /etc/services.d/*/run \
    && chmod +x /etc/services.d/*/finish

# Install Python package
COPY usbip_addon /usr/local/lib/python3.11/usbip_addon/

# Make usbip_addon importable
ENV PYTHONPATH=/usr/local/lib/python3.11
