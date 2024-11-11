ARG BUILD_FROM
FROM $BUILD_FROM

# This tags our image with the correct tags for HA
LABEL \
  io.hass.version="VERSION" \
  io.hass.type="addon" \
  io.hass.arch="armhf|aarch64|i386|amd64|armv7"

# Since we target the python docker images, there's no need to install python.

# Copy our PY requirements and install them.
COPY requirements.txt /
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt

# Install the optional pacakges for zstandard compression. The extra packages are required for the zstandard python pip install to build.
# Note we don't isntall PY 3 so it doesn't exit and we don't accitentally use it instaed of the base image python.
# hadolint ignore=DL3018,DL3019,DL3059
RUN apk add zstd python3-dev gcc zlib-dev musl-dev libffi-dev
# THIS VERSION STRING MUST STAY IN SYNC with Compression.ZStandardPipPackageString
RUN pip install --no-cache-dir -q "zstandard>=0.21.0,<0.23.0"

# Create a working dir for all of our files.
WORKDIR /app

# Copy all of our files in.
COPY . ./

# Helpful for debugging.
RUN ls
RUN whoami

#
# Since we are running in a docker container, there's no need for dynamic configs.
# The repo root is /app, as we define in our Dockerfile
# The storage root it the Home Assistant docker mapped dir, /data/
#   Note that all of the data is stored in this flat dir, the config, storage data, and logs.
#
# This is the json config, that's base64 encoded and sent as an argument.
# { "VersionFileDir":"/app", "AddonDataRootDir":"/data", "StorageDir":"/data", "LogsDir":"/data", "IsRunningInHaAddonEnv":true }
#
# Note if we every move past "python3", we need to update apparmor.
CMD [ "python3", "-m", "homeway_linuxhost", "eyAiVmVyc2lvbkZpbGVEaXIiOiIvYXBwIiwgIkFkZG9uRGF0YVJvb3REaXIiOiIvZGF0YSIsICJTdG9yYWdlRGlyIjoiL2RhdGEiLCAiTG9nc0RpciI6Ii9kYXRhIiwgIklzUnVubmluZ0luSGFBZGRvbkVudiI6dHJ1ZSB9" ]

# Build arguments
ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION
ARG BUILD_NAME
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION

# Labels
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    maintainer="Homeway Team <support@homeway.io>" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.vendor="Homewayi.io" \
    org.opencontainers.image.authors="Homeway Team <support@homeway.io>" \
    org.opencontainers.image.licenses="GNU Version 3" \
    org.opencontainers.image.url="https://homeway.io" \
    org.opencontainers.image.source="https://github.com/homewayio/addon" \
    org.opencontainers.image.documentation="https://github.com/homewayio/addon/blob/main/README.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}