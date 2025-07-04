#!/usr/bin/with-contenv bashio

# Since we are running in a docker container, there's no need for dynamic configs.
# The repo root is /app, as we define in our Dockerfile
# The storage root is the Home Assistant docker mapped dir, /data/
#   Note that all of the data is stored in this flat dir, the config, storage data, and logs.

# This is the json config, that's base64 encoded and sent as an argument.
# { "VersionFileDir":"/app", "AddonDataRootDir":"/data", "StorageDir":"/data", "LogsDir":"/data", "IsRunningInHaAddonEnv":true }

python3 -m homeway_linuxhost eyAiVmVyc2lvbkZpbGVEaXIiOiIvYXBwIiwgIkFkZG9uRGF0YVJvb3REaXIiOiIvZGF0YSIsICJTdG9yYWdlRGlyIjoiL2RhdGEiLCAiTG9nc0RpciI6Ii9kYXRhIiwgIklzUnVubmluZ0luSGFBZGRvbkVudiI6dHJ1ZSB9
