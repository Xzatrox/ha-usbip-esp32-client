ARG BUILD_FROM
FROM ${BUILD_FROM}

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

# Ensure all s6 scripts are executable
RUN chmod +x /etc/cont-init.d/*.py \
    && chmod +x /etc/cont-finish.d/*.py \
    && chmod +x /etc/services.d/*/run \
    && chmod +x /etc/services.d/*/finish

# Install Python package
COPY usbip_addon /usr/local/lib/python3.11/usbip_addon/

# Make usbip_addon importable
ENV PYTHONPATH=/usr/local/lib/python3.11
