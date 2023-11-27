import os
import yaml

class Version:

    # Parses the common plugin version from the config.yaml.
    # Throws if the file can't be found or the version string can't be found.
    @staticmethod
    def GetPluginVersion(repoRoot):
        # Use the dockerfile, so it's the source of truth.
        versionFilePath = os.path.join(repoRoot, "config.yaml")
        if os.path.exists(versionFilePath) is False:
            raise Exception("Failed to find our repo root setup file to parse the version. Expected Path: "+versionFilePath)

        # Read the file, find the version string.
        with open(versionFilePath, "r", encoding="utf-8") as f:
            parsedYaml = yaml.safe_load(f)
            if "version" not in parsedYaml:
                raise Exception(f"Version key in yaml file: {versionFilePath}")
            return parsedYaml["version"]
