#!/usr/bin/with-contenv bashio

# Since we are running in a docker container, there's no need for dynamic configs.
# The repo root is /app, as we define in our Dockerfile
# The storage root it the Home Assistant docker mapped dir, /data/

# This is the json config, that's base64 encoded and sent as an argument.
# { "RepoRootDir":"/app", "StorageDir":"/data", "RunningInAddonContext":true, "HomeAssistantIp":"127.0.0.1", "HomeAssistantPort":8123 }

python3 -m homeway_linuxhost eyAiUmVwb1Jvb3REaXIiOiIvYXBwIiwgIlN0b3JhZ2VEaXIiOiIvZGF0YSIsICJSdW5uaW5nSW5BZGRvbkNvbnRleHQiOnRydWUsICJIb21lQXNzaXN0YW50SXAiOiIxMjcuMC4wLjEiLCAiSG9tZUFzc2lzdGFudFBvcnQiOjgxMjMgfQ==