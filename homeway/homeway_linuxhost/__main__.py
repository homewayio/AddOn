import os
import sys
import json
import base64
from enum import Enum

from homeway.Proto.AddonTypes import AddonTypes

from .linuxhost import LinuxHost

#
# Helper functions for config parsing and validation.
#
class ConfigDataTypes(Enum):
    String = 1
    Path = 2
    Bool = 3
    Int = 4


def _GetConfigVarAndValidate(config, varName:str, dataType:ConfigDataTypes):
    if varName not in config:
        raise Exception(f"{varName} isn't found in the json config.")
    var = config[varName]

    if var is None:
        raise Exception(f"{varName} returned None when parsing json config.")

    if dataType == ConfigDataTypes.String or dataType == ConfigDataTypes.Path:
        var = str(var)
        if len(var) == 0:
            raise Exception(f"{varName} is an empty string.")

        if dataType == ConfigDataTypes.Path:
            if os.path.exists(var) is False:
                raise Exception(f"{varName} is a path, but the path wasn't found.")

    elif dataType == ConfigDataTypes.Bool:
        var = bool(var)

    elif dataType == ConfigDataTypes.Int:
        var = int(var)

    else:
        raise Exception(f"{varName} has an invalid config data type. {dataType}")
    return var

#
# Helper for errors.
#
def _PrintErrorAndExit(msg:str):
    print(f"\r\nPlugin Init Error - {msg}", file=sys.stderr)
    print( "\r\nPlease contact support so we can fix this for you! support@homeway.io", file=sys.stderr)
    sys.exit(1)


if __name__ == '__main__':
    # The config and settings path is passed as the first arg when the service runs.
    # This allows us to run multiple services instances, each pointing at it's own config.
    if len(sys.argv) < 1:
        _PrintErrorAndExit("No program and json settings path passed to service")

    # The second arg should be a json string, which has all of our params.
    if len(sys.argv) < 2:
        _PrintErrorAndExit("No json settings path passed to service")

    # Try to parse the config
    jsonConfigStr = None
    try:
        # The args are passed as a urlbase64 encoded string, to prevent issues with passing some chars as args.
        argsJsonBase64 = sys.argv[1]
        jsonConfigStr = base64.urlsafe_b64decode(bytes(argsJsonBase64, "utf-8")).decode("utf-8")
        print("Loading Service Config: "+jsonConfigStr)
        config = json.loads(jsonConfigStr)

        #
        # Parse the common, required args.
        #
        VersionFileDir = _GetConfigVarAndValidate(config, "VersionFileDir", ConfigDataTypes.Path)
        AddonDataRootDir = _GetConfigVarAndValidate(config, "AddonDataRootDir", ConfigDataTypes.Path)
        LogsDir = _GetConfigVarAndValidate(config, "LogsDir", ConfigDataTypes.Path)
        StorageDir = _GetConfigVarAndValidate(config, "StorageDir", ConfigDataTypes.Path)
        IsRunningInHaAddonEnv = _GetConfigVarAndValidate(config, "IsRunningInHaAddonEnv", ConfigDataTypes.Bool)

        # This is an optional arg added for the standalone docker version.
        # If it doesn't exist, the value is False.
        IsRunningAsStandaloneDocker = False
        if "IsRunningAsStandaloneDocker" in config:
            IsRunningAsStandaloneDocker = _GetConfigVarAndValidate(config, "IsRunningAsStandaloneDocker", ConfigDataTypes.Bool)


    except Exception as e:
        _PrintErrorAndExit(f"Exception while loading json config. Error:{str(e)}, Config: {jsonConfigStr}")

    # For debugging, we also allow an optional dev object to be passed.
    devConfig_CanBeNone = None
    try:
        if len(sys.argv) > 2:
            devConfig_CanBeNone = json.loads(sys.argv[2])
            print("Using dev config: "+sys.argv[2])
    except Exception as e:
        _PrintErrorAndExit(f"Exception while DEV CONFIG. Error:{str(e)}, Config: {sys.argv[2]}")

    # Run!
    try:
        # Get the addon type.
        addon = AddonTypes.StandaloneCli
        if IsRunningInHaAddonEnv:
            addon = AddonTypes.HaAddon
        if IsRunningAsStandaloneDocker:
            addon = AddonTypes.StandaloneDocker

        # Create and run the main host!
        host = LinuxHost(AddonDataRootDir, LogsDir, addon, devConfig_CanBeNone)
        host.RunBlocking(StorageDir, VersionFileDir, devConfig_CanBeNone)
    except Exception as e:
        _PrintErrorAndExit(f"Exception leaked from main host class. Error:{str(e)}")

    # If we exit here, it's due to an error, since RunBlocking should be blocked forever.
    sys.exit(1)
