#!/usr/bin/with-contenv bashio

# Since we are running in a docker container, there's no need for dynamic configs.
# The repo root is /app, as we define in our Dockerfile
# The storage root it the Home Assistant docker mapped dir, /data/

# This is the json config, that's base64 encoded and sent as an argument.
# { "RepoRootDir": "/app", "StorageDir": "/data", "RunningInAddonContext": true }

python3 -m homeway_linuxhost eyAiUmVwb1Jvb3REaXIiOiAiL2FwcCIsICJTdG9yYWdlRGlyIjogIi9kYXRhIiwgIlJ1bm5pbmdJbkFkZG9uQ29udGV4dCI6IHRydWUgfQ==