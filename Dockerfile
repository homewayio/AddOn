#
# NOTE: This is the Dockerfile for the Homeway Standalone Docker image.
# This is NOT the Homeway Home Assistant add-on docker container!
# See ./homeway/Dockerfile for the Home Assistant add-on container.
#

# Start with the latest alpine, for a solid base to build from.
FROM alpine:3.20.0

# This is a special dir that the user MUST mount to the host, so that the data is persisted.
# If this is not mounted, the addon will need to be re-linked every time the container is remade.
ENV DATA_DIR=/data/

# Define some user vars we will use for the image.
# These are read in the docker_homeway module, so they must not change!
ENV USER=root
ENV REPO_DIR=/root/homeway
ENV VENV_DIR=/root/homeway-env

# We will base ourselves in root, because why not.
WORKDIR /root

# Install the required packages.
# Any packages here should be mirrored in the cli standalone installer script - and any optional pillow packages done inline.
# G++, python3-dev, libffi-dev are required to build the zstandard package on arm.
RUN apk add --no-cache curl python3 py3-pip py3-virtualenv g++ python3-dev libffi-dev

# We decided to not run the installer, since the point of the installer is to setup the env, build the launch args, and setup the service.
# Instead, we will manually run the smaller subset of commands that are required to get the env setup in docker.
# Note that if this ever becomes too much of a hassle, we might want to revert back to using the installer, and supporting a headless install.
# hadolint ignore=DL3059
RUN virtualenv -p /usr/bin/python3 ${VENV_DIR}
# hadolint ignore=DL3013 DL3042 DL3059
RUN ${VENV_DIR}/bin/python -m pip install --upgrade pip

# Copy the entire repo into the image, do this as late as possible to avoid rebuilding the image every time the repo changes.
COPY ./ ${REPO_DIR}/
RUN ${VENV_DIR}/bin/pip3 install --require-virtualenv --no-cache-dir -q -r ${REPO_DIR}/homeway/requirements.txt

# Install the optional packages for zstandard compression.
# THIS VERSION STRING MUST STAY IN SYNC with Compression.ZStandardPipPackageString
# hadolint ignore=DL3059
RUN apk add --no-cache zstd
# hadolint ignore=DL3059
RUN ${VENV_DIR}/bin/pip3 install --require-virtualenv --no-cache-dir -q "zstandard>=0.21.0,<0.23.0"

# For docker, we use our homeway_standalone_docker host to handle the runtime setup and launch of the service.
WORKDIR ${REPO_DIR}

# Use the full path to the venv, we must use this [] notation for our ctl-c handler to work in the container
ENTRYPOINT ["/root/homeway-env/bin/python", "-m", "homeway_standalone_docker"]
