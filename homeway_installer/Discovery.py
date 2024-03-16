import os

from .Logging import Logger
from .Context import Context
from .Util import Util
from .ConfigFile import ConfigFile

# This class is used to find existing instances running on this device.
class Discovery:
    # This is the base data folder name that will be used.
    # For any instance past #1, a number will be appended on the end of the folder in _1 format.
    # The folders will always be in the user's home path.
    c_AddonRootFolder_Lower = ".homeway-addon"

    def Discovery(self, context:Context):
        Logger.Debug("Starting addon discovery.")

        # Look for existing addon data installs.
        existingAddonFolders = []
        # Sort so the folder we find are ordered from 1-... This makes the selection process nicer, since the ID == selection.
        fileAndDirList = sorted(os.listdir(context.UserHomePath))
        for fileOrDirName in fileAndDirList:
            if fileOrDirName.lower().startswith(Discovery.c_AddonRootFolder_Lower):
                existingAddonFolders.append(fileOrDirName)
                Logger.Debug(f"Found existing data folder: {fileOrDirName}")

        # If we are in update mode and we only found one instance, just select it.
        if context.IsUpdateMode and len(existingAddonFolders) == 1:
            self._SetupContextFromVars(context, existingAddonFolders[0])
            Logger.Info(f"Existing addon instance selected. Path: {context.AddonFolder}, Id: {context.InstanceId}")
            return

        # If there's an existing folders, ask the user if they want to use them.
        if len(existingAddonFolders) > 0:
            count = 1
            Logger.Blank()
            Logger.Blank()
            Logger.Header("Existing Homeway Addons Found")
            Logger.Blank()
            Logger.Blank()
            Logger.Info( "If you want to update or re-setup an instance, select instance id.")
            Logger.Info( "                        - or - ")
            Logger.Info( "If you want to install a new instance, select 'n'.")
            Logger.Blank()
            Logger.Info("Options:")
            for folder in existingAddonFolders:
                addonId = self._GetAddonIdFromFolderName(folder)
                # Try to parse the config, if there is one and it's valid.
                ip, port, _ = ConfigFile.TryToParseConfig(os.path.join(context.UserHomePath, folder))
                if ip is None and port is None:
                    Logger.Info(f"  {count}) Instance id {addonId} - Path: ~/{folder}")
                else:
                    Logger.Info(f"  {count}) Instance id {addonId} - Server: {ip}:{port}")
                count += 1
            Logger.Info("  n) Setup a new Homeway addon instance")
            Logger.Blank()
            # Ask the user which number they want.
            responseInt = -1
            isFirstPrint = True
            while True:
                try:
                    if isFirstPrint:
                        isFirstPrint = False
                    else:
                        Logger.Warn( "If you need help, contact us! https://homeway.io/support")
                    response = input("Enter an instance id or 'n': ")
                    response = response.lower().strip()
                    # If the response is n, fall through.
                    if response == "n":
                        break
                    # Parse the input and -1 it, so it aligns with the array length.
                    tempInt = int(response.lower().strip()) - 1
                    if tempInt >= 0 and tempInt < len(existingAddonFolders):
                        responseInt = tempInt
                        break
                    Logger.Warn("Invalid number selection, try again.")
                except Exception as _:
                    Logger.Warn("Invalid input, try again.")

            # If there is a response, the user selected an instance.
            if responseInt != -1:
                # Use this instance
                self._SetupContextFromVars(context, existingAddonFolders[responseInt])
                Logger.Info(f"Existing addon instance selected. Path: {context.AddonFolder}, Id: {context.InstanceId}")
                return

        # Create a new instance path. Either there is no existing data path or the user wanted to create a new one.
        # Since we have all of the data paths, we will make this new instance id be the count + 1.
        # For the first instance, we dont append a number, we just use the base folder name.
        newId = len(existingAddonFolders) + 1
        addonFolderPath = ""
        if newId == 1:
            addonFolderPath = Discovery.c_AddonRootFolder_Lower
        else:
            addonFolderPath = f"{Discovery.c_AddonRootFolder_Lower}-{newId}"
        self._SetupContextFromVars(context, addonFolderPath)
        Logger.Info(f"Creating a new addon data path. Path: {context.AddonFolder}, Id: {context.InstanceId}")
        return


    def _SetupContextFromVars(self, context:Context, folderName:str):
        # First, ensure we can parse the id and set it.
        context.InstanceId = self._GetAddonIdFromFolderName(folderName)
        # If this is the primary instance, set the flag so others know.
        context.IsPrimaryInstance = context.InstanceId == "1"
        # Make the full path
        context.AddonFolder = os.path.join(context.UserHomePath, folderName)
        # Ensure the file exists and we have permissions
        Util.EnsureDirExists(context.AddonFolder, context, True)


    def _GetAddonIdFromFolderName(self, folderName:str):
        folderName_lower = folderName.lower()
        # For instance #1, it will be just the folder name. For extra instances there will be a _#
        # appended on the folder.
        if folderName_lower.startswith(Discovery.c_AddonRootFolder_Lower) is True:
            idSeparatorIndex =  folderName_lower.find("_")
            if idSeparatorIndex == -1:
                return "1"
            return folderName_lower[idSeparatorIndex+1:]
        Logger.Error(f"We tried to get an addon id from a non-addon data folder. {folderName}")
        raise Exception("We tried to get an addon id from a non-addon data folder")
