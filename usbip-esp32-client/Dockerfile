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

# Copy add-on source (s6-overlay scripts and rootfs structure)
COPY rootfs /

# Install Python package
COPY usbip_addon /usr/local/lib/python3.11/usbip_addon/

# s6-overlay will handle service startup
