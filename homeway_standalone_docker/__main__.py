import os
import sys
import json
import time
import signal
import base64
import logging
import traceback
import subprocess
from typing import Any, Optional

#
# This standalone docker host is the entry point for the docker container.
# If you're looking for the Home Assistant addon, check the /homeway dir.
#

from homeway.homeway_linuxhost.config import Config

# pylint: disable=logging-fstring-interpolation

if __name__ == '__main__':

    # Setup a basic logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    std = logging.StreamHandler(sys.stdout)
    std.setFormatter(formatter)
    logger.addHandler(std)

    logger.info("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
    logger.info("  Starting Homeway Standalone Docker Bootstrap")
    logger.info("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")

    #
    # Helper functions
    #
    def LogException(msg:str, e:Exception) -> None:
        tb = traceback.format_exc()
        exceptionClassType = "unknown_type"
        if e is not None:
            exceptionClassType = e.__class__.__name__
        logger.error(f"{msg}; {str(exceptionClassType)} Exception: {str(e)}; {str(tb)}")

    def EnsureIsPath(path:Optional[str]) -> str:
        logger.info(f"Ensuring path exists: {path}")
        if path is None or not os.path.exists(path):
            raise Exception(f"Path does not exist: {path}")
        return path

    def CreateDirIfNotExists(path: str) -> None:
        if not os.path.exists(path):
            os.makedirs(path)

    try:
        # First, read the required env vars that are set in the dockerfile.
        logger.info(f"Env Vars: {os.environ}")
        virtualEnvPath = EnsureIsPath(os.environ.get("VENV_DIR", None))
        repoRootPath = EnsureIsPath(os.environ.get("REPO_DIR", None))
        dataPath = EnsureIsPath(os.environ.get("DATA_DIR", None))

        # For the standalone docker, we always put the config in the data dir.
        configPath = dataPath

        # Create the config object, which will read an existing config or make a new one.
        # If this is the first run, there will be no config file, so we need to create one.
        logger.info(f"Init config object: {configPath}")
        config = Config(configPath)

        # Get the HA IP var from the user.
        haIpOrHostname = os.environ.get("HOME_ASSISTANT_IP", None)
        if haIpOrHostname is not None:
            logger.info(f"Setting Home Assistant IP or Hostname: {haIpOrHostname}")
            config.SetStr(Config.HomeAssistantSection, Config.HaIpOrHostnameKey, haIpOrHostname)
        # Ensure something is set now.
        if config.GetStr(Config.HomeAssistantSection, Config.HaIpOrHostnameKey, None) is None:
            logger.error("")
            logger.error("")
            logger.error("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
            logger.error("          You must provide the IP address or hostname of your Home Assistant server.")
            logger.error("  Use `docker run -e HOME_ASSISTANT_IP=<ip or hostname>` or add it to your docker-compose file.")
            logger.error("")
            logger.error("           The value can be a hostname like `localhost` or `homeassistant.local`")
            logger.error("                    or an IP address like `127.0.0.1` or `192.168.1.10`")
            logger.error("")
            logger.error("                If you need help, contact us -> https://homeway.io/support")
            logger.error("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
            logger.error("")
            logger.error("")
            # Sleep some, so we don't restart super fast and then exit.
            time.sleep(5.0)
            sys.exit(1)

        # Get the home assistant port, but default to 8123 if it's not set.
        haPort = os.environ.get("HOME_ASSISTANT_PORT", None)
        if haPort is not None:
            logger.info(f"Setting Home Assistant Port: {haPort}")
            config.SetStr(Config.HomeAssistantSection, Config.HaPortKey, haPort)
        # Ensure something is set now.
        if config.GetStr(Config.HomeAssistantSection, Config.HaPortKey, None) is None:
            logger.info("Setting Home Assistant Port to the default value of 8123.")
            haPort = "8123"
            config.SetStr(Config.HomeAssistantSection, Config.HaPortKey, haPort)

        # Get the HA access token
        haAccessToken = os.environ.get("HOME_ASSISTANT_ACCESS_TOKEN", None)
        if haAccessToken is not None:
            logger.info("Setting Home Assistant Access Token.")
            config.SetStr(Config.HomeAssistantSection, Config.HaAccessTokenKey, haAccessToken)
        # Ensure something is set now.
        if config.GetStr(Config.HomeAssistantSection, Config.HaAccessTokenKey, None) is None:
            logger.error("")
            logger.error("")
            logger.error("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
            logger.error("            You must provide a Long-Lived Access Token from your Home Assistant server.")
            logger.error("  Use `docker run -e HOME_ASSISTANT_ACCESS_TOKEN=<token>` or add it to your docker-compose file.")
            logger.error("")
            # Note this text is duplicated in the homeway_installer logic and the docker-readme.md file.
            logger.error("To create a Long-Lived Access Token in Home Assistant, follow these steps:")
            logger.error("  1) Open the Home Assistant web UI and go to your user profile:")
            logger.error(f"       http://{haIpOrHostname}:{haPort}/profile")
            logger.error("  2) On your profile page, click the 'Security' tab at the top.")
            logger.error("  3) Scroll down to the bottom, to the 'Long-lived access tokens' box.")
            logger.error("  4) Click 'CREATE TOKEN'")
            logger.error("  5) Enter any name, something 'Homeway Addon' works just fine.")
            logger.error("  6. Copy the access token and use it in the docker run command line or your docker compose file.")
            logger.error("")
            logger.error("              If you need help, contact us -> https://homeway.io/support")
            logger.error("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
            logger.error("")
            logger.error("")
            # Sleep some, so we don't restart super fast and then exit.
            time.sleep(5.0)
            sys.exit(1)

        # Check for the remote access enabled var.
        remoteAccessEnabledStr = os.environ.get("ENABLE_REMOTE_ACCESS", None)
        if remoteAccessEnabledStr is not None:
            remoteAccessEnabled = remoteAccessEnabledStr.lower() in ["1", "true", "yes", "on"]
            logger.info(f"Setting Homeway Remote Access Enabled to: {str(remoteAccessEnabled)}")
            config.SetBool(Config.HomeAssistantSection, Config.HaEnableRemoteAccess, remoteAccessEnabled)

        # Create the rest of the required dirs based in the data dir, since it's persistent.
        localStoragePath = os.path.join(dataPath, "homeway-store")
        CreateDirIfNotExists(localStoragePath)
        logDirPath = os.path.join(dataPath, "logs")
        CreateDirIfNotExists(logDirPath)

        # Build the launch string
        launchConfig = {
            "VersionFileDir" : os.path.join(repoRootPath, "homeway"),
            "AddonDataRootDir" : configPath,
            "LogsDir" : logDirPath,
            "StorageDir" : localStoragePath,
            "IsRunningInHaAddonEnv" : False,
            "IsRunningAsStandaloneDocker" : True
        }

        # Convert the launch string into what's expected.
        launchConfigStr = json.dumps(launchConfig)
        logger.info(f"Launch config: {launchConfigStr}")
        base64EncodedLaunchConfig =  base64.urlsafe_b64encode(bytes(launchConfigStr, "utf-8")).decode("utf-8")

        # Setup a ctl-c handler, so the docker container can be closed easily.
        def signal_handler(sig:Any, frame:Any):
            logger.info("Homeway Standalone Docker container stop requested")
            sys.exit(0)
        signal.signal(signal.SIGINT, signal_handler)

        # Instead of running the addon in our process, we decided to launch a different process so it's clean and runs
        # just like the addon normally runs.
        pythonPath = os.path.join(virtualEnvPath, os.path.join("bin", "python3"))
        modulePath = os.path.join(repoRootPath, "homeway")
        logger.info(f"Launch PY path: {pythonPath}, module path: {modulePath}")
        result:subprocess.CompletedProcess = subprocess.run([pythonPath, "-m", "homeway_linuxhost", base64EncodedLaunchConfig],
                                                            cwd=modulePath, check=False)

        # Normally the process shouldn't exit unless it hits a bad error.
        if result.returncode == 0:
            logger.info(f"Homeway standalone docker container exited. Result: {result.returncode}")
        else:
            logger.error(f"Homeway standalone docker container exited with an error. Result: {result.returncode}")

    except Exception as e:
        LogException("Exception while bootstrapping Homeway Standalone Docker.", e)

    # Sleep for a bit, so if we are restarted we don't do it instantly.
    time.sleep(3)
    sys.exit(1)
