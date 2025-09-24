import os
import json
import base64
from typing import Tuple

from .Util import Util
from .Logging import Logger
from .Context import Context

# Responsible for creating, running, and ensuring the service is installed and running.
class Service:

    def Install(self, context:Context) -> None:
        Logger.Header("Setting Up System Service...")

        # We always re-write the service file, to make sure it's current.
        if os.path.exists(context.ServiceFilePath):
            Logger.Info("Service file already exists, recreating.")

        # Create the service file.

        # First, we create a json object that we use as arguments. Using a json object makes parsing and such more flexible.
        # We base64 encode the json string to prevent any arg passing issues with things like quotes, spaces, or other chars.
        # Note that the VersionFileDir points to where the config.yaml sits. This is the file that defines the version of the addon
        # for all things.
        argsJson:str = json.dumps({
            'VersionFileDir': os.path.join(context.RepoRootFolder, "homeway"),
            'AddonDataRootDir': context.AddonFolder,
            'LogsDir': context.LogFolder,
            'StorageDir': context.LocalDataFolder,
            'IsRunningInHaAddonEnv': False,
        })
        # We have to convert to bytes -> encode -> back to string.
        argsJsonBase64:str = base64.urlsafe_b64encode(bytes(argsJson, "utf-8")).decode("utf-8")

        # Base on the OS type, install the service differently
        self._InstallDebian(context, argsJsonBase64)


    # Install for debian setups
    def _InstallDebian(self, context:Context, argsJsonBase64:str) -> None:
        # Note we use root as a user instead of the installing user
        # to make sure we have access to the HA config file for updates if needed
        s:str = f'''\
    # Homeway Addon Service
    [Unit]
    Description=Homeway
    # Start after network has started
    After=network-online.target

    [Install]
    WantedBy=multi-user.target

    # Simple service, targeting the user that was used to install the service, simply running our homeway py host script.
    [Service]
    Type=simple
    User=root
    WorkingDirectory={context.RepoRootFolder}/homeway
    ExecStart={context.VirtualEnvPath}/bin/python3 -m homeway_linuxhost "{argsJsonBase64}"
    Restart=always
    # Since we will only restart on a fatal Logger.Error, set the restart time to be a bit higher, so we don't spin and spam.
    RestartSec=10
'''
        if context.SkipSudoActions:
            Logger.Warn("Skipping service file creation, registration, and starting due to skip sudo actions flag.")
            return

        Logger.Debug("Service config file contents to write: "+s)
        Logger.Info("Creating service file "+context.ServiceFilePath+"...")
        with open(context.ServiceFilePath, "w", encoding="utf-8") as serviceFile:
            serviceFile.write(s)

        Logger.Info("Registering service...")
        Util.RunShellCommand("systemctl enable "+context.ServiceName)
        Util.RunShellCommand("systemctl daemon-reload")

        # Stop and start to restart any running services.
        Logger.Info("Starting service...")
        Service.RestartDebianService(context.ServiceName)

        Logger.Info("Service setup and start complete!")


    @staticmethod
    def RestartDebianService(serviceName:str, throwOnBadReturnCode:bool=True) -> None:
        result_tuple:Tuple[int,str,str] = Util.RunShellCommand("systemctl stop "+serviceName, throwOnBadReturnCode)
        (returnCode, output, errorOut) = result_tuple
        if returnCode != 0:
            Logger.Warn(f"Service {serviceName} might have failed to stop. Output: {output} Error: {errorOut}")
        result_tuple = Util.RunShellCommand("systemctl start "+serviceName, throwOnBadReturnCode)
        (returnCode, output, errorOut) = result_tuple
        if returnCode != 0:
            Logger.Warn(f"Service {serviceName} might have failed to start. Output: {output} Error: {errorOut}")
