import os
import time
import configparser
import requests

from homeway.homeway_linuxhost.secrets import Secrets
from homeway.homeway.hostcommon import HostCommon

from .Util import Util
from .Logging import Logger
from .Context import Context

# Responsible for getting the addon id from the instance's config file, checking if it's linked,
# and if not helping the user link their addon to Homeway.
class Linker:

    def Run(self, context:Context):

        # First, wait for the config file to be created and the addon ID to show up.
        addonId = None
        startTimeSec = time.time()
        Logger.Info("Waiting for the Homeway Addon to produce an addon id... (this can take a few seconds)")
        while addonId is None:
            # Give the service time to start.
            time.sleep(0.1)

            # Try to get the addon id from the secrets config file
            addonId = Linker.GetAddonIdFromServiceSecretsConfigFile(context)

            # If we failed, try to handle the case where the service might be having an error.
            if addonId is None:
                timeDelta = time.time() - startTimeSec
                if timeDelta > 10.0:
                    Logger.Warn("The standalone addon service is taking a while to start, there might be something wrong.")
                    if Util.AskYesOrNoQuestion("Do you want to keep waiting?"):
                        startTimeSec = time.time()
                        continue
                    # Handle the error and cleanup.
                    Logger.Blank()
                    Logger.Blank()
                    Logger.Error("We didn't get a response from the Homeway addon service when waiting for the addon id.")
                    Logger.Error("You can find service logs which might indicate the error in: "+context.LogFolder)
                    Logger.Blank()
                    Logger.Blank()
                    Logger.Error("Attempting to print the service logs:")
                    # Try to print the service logs to the console.
                    Util.PrintServiceLogsToConsole(context)
                    raise Exception("Failed to read addon id from service config file.")

        # Check if the addon is already connected to an account.
        # If so, report and we don't need to do the setup.
        (isConnectedToService, addonNameIfConnectedToAccount) = self._IsAddonConnectedToAnAccount(addonId)
        if isConnectedToService and addonNameIfConnectedToAccount is not None:
            Logger.Header("This addon is securely connected to your Homeway account as '"+str(addonNameIfConnectedToAccount)+"'")
            return

        # The addon isn't connected to an account.
        # If this is not the first time setup, ask the user if they want to do it now.
        if context.ExistingAddonId is not None:
            Logger.Blank()
            Logger.Warn("This addon isn't connected to a Homeway account.")
            if Util.AskYesOrNoQuestion("Would you like to link it now?") is False:
                Logger.Blank()
                Logger.Header("You can connect this addon anytime, using this URL: ")
                Logger.Warn(self._GetAddAddonUrl(addonId))
                return

        # Help the user setup the addon!
        Logger.Blank()
        Logger.Blank()
        Logger.Warn( "You're 10 seconds away from free Home Assistant remote access!")
        Logger.Blank()
        self._PrintShortCodeStyleOrFullUrl(addonId)
        Logger.Blank()
        Logger.Blank()

        Logger.Info("Waiting for the addon to be linked to your account...")
        startTimeSec = time.time()
        notConnectedTimeSec = time.time()
        while True:
            # Query status.
            (isConnectedToService, addonNameIfConnectedToAccount) = self._IsAddonConnectedToAnAccount(addonId)

            if addonNameIfConnectedToAccount is not None:
                # Connected!
                Logger.Blank()
                Logger.Header("Success! This addon is securely connected to your account as '"+str(addonNameIfConnectedToAccount)+"'")
                return

            # We expect the addon to be connected to the service. If it's not, something might be wrong.
            if isConnectedToService is False:
                notConnectedDeltaSec = time.time() - notConnectedTimeSec
                Logger.Info("Waiting for the addon to connect to our service...")
                if notConnectedDeltaSec > 10.0:
                    Logger.Warn("It looks like your addon hasn't connected to the service yet, which it should have by now.")
                    if Util.AskYesOrNoQuestion("Do you want to keep waiting?"):
                        notConnectedTimeSec = time.time()
                        continue
                    # Handle the Logger.Error and cleanup.
                    Logger.Blank()
                    Logger.Blank()
                    Logger.Error("The addon hasn't connected to our service yet. Something might be wrong.")
                    Logger.Error("You can find service logs which might indicate the Logger.Error in: "+context.LogFolder)
                    Logger.Blank()
                    Logger.Blank()
                    Logger.Error("Attempting to print the service logs:")
                    # Try to print the service logs to the console.
                    Util.PrintServiceLogsToConsole(context)
                    raise Exception("Failed to wait for addon to connect to service.")
            else:
                # The addon is connected but no user account is connected yet.
                timeDeltaSec = time.time() - startTimeSec
                if timeDeltaSec > 60.0:
                    Logger.Warn("It doesn't look like this addon has been connected to your account yet.")
                    if Util.AskYesOrNoQuestion("Do you want to keep waiting?"):
                        Logger.Blank()
                        Logger.Blank()
                        self._PrintShortCodeStyleOrFullUrl(addonId)
                        Logger.Blank()
                        startTimeSec = time.time()
                        continue

                    Logger.Blank()
                    Logger.Blank()
                    Logger.Blank()
                    Logger.Warn("You can use the following URL at anytime to link this addon to your account. Or run this install script again for help.")
                    Logger.Header(self._GetAddAddonUrl(addonId))
                    Logger.Blank()
                    Logger.Blank()
                    return

            # Sleep before trying the API again.
            time.sleep(1.0)


    def _PrintShortCodeStyleOrFullUrl(self, addonId):
        # To make the setup easier, we will present the user with a short code if we can get one.
        # If not, fallback to the full URL.
        try:
            # Try to get a short code. We do a quick timeout so if this fails, we just present the user the longer URL.
            # Any failures, like rate limiting, server error, whatever, and we just use the long URL.
            r = requests.post('https://homeway.io/api/shortcode/create', json={"Type": 1, "PluginId": addonId}, timeout=10.0)
            if r.status_code == 200:
                jsonResponse = r.json()
                if "Result" in jsonResponse and "Code" in jsonResponse["Result"]:
                    codeStr = jsonResponse["Result"]["Code"]
                    if len(codeStr) > 0:
                        Logger.Warn("To securely link this addon to your Homeway account, go to the following website and use the code.")
                        Logger.Blank()
                        Logger.Header("Website: https://homeway.io/code")
                        Logger.Header("Code:    "+codeStr)
                        return
        except Exception:
            pass

        Logger.Warn("Use this URL to securely link this addon to your Homeway account:")
        Logger.Header(self._GetAddAddonUrl(addonId))


    # Get's the addon id from the instances secrets config file, if the config exists.
    @staticmethod
    def GetAddonIdFromServiceSecretsConfigFile(context:Context) -> str:
        # This path and name must stay in sync with where the addon will write the file.
        addonServiceConfigFilePath = os.path.join(context.LocalDataFolder, Secrets.FileName)

        # Check if there is a file. If not, it means the service hasn't been run yet and this is a first time setup.
        if os.path.exists(addonServiceConfigFilePath) is False:
            return None

        # If the file exists, try to read it.
        # If this fails, let it throw, so the user knows something is wrong.
        Logger.Debug("Found existing Homeway service secrets config.")
        try:
            config = configparser.ConfigParser(allow_no_value=True, strict=False)
            config.read(addonServiceConfigFilePath)
        except Exception as e:
            # Print the file for Logger.Debugging.
            Logger.Info("Failed to read config file. "+str(e)+ ", trying again...")
            with open(addonServiceConfigFilePath, 'r', encoding="utf-8") as f:
                Logger.Debug("file contents:"+f.read())
            return None

        # Print the raw config file for debugging issues with the config.
        try:
            with open(addonServiceConfigFilePath, 'r', encoding="utf-8") as f:
                Logger.Debug("Service secrets config contents:"+f.read())
        except Exception:
            pass

        # Try to find the values.
        if config.has_section(Secrets.SecretsSection) is False:
            Logger.Debug("Server section not found in Homeway config.")
            return None
        if Secrets.PluginIdKey not in config[Secrets.SecretsSection].keys():
            Logger.Debug("Addon id not found in Homeway config.")
            return None
        addonId = config[Secrets.SecretsSection][Secrets.PluginIdKey]
        if len(addonId) < HostCommon.c_PluginIdMaxLength:
            Logger.Debug("Addon ID found, but the length is less than "+str(HostCommon.c_PluginIdMaxLength)+" chars? value:`"+addonId+"`")
            return None
        return addonId


    # Checks with the service to see if the addon is setup on a account.
    # Returns a tuple of two values
    #   1 - bool - Is the addon connected to the service
    #   2 - string - If the addon is setup on an account, the addon name.
    def _IsAddonConnectedToAnAccount(self, addonId):
        # Adding retry logic, since one call can fail if the server is updating or whatever.
        attempt = 0
        while True:
            try:
                # Keep track of attempts and timeout if there have been too many.
                attempt += 1
                if attempt > 5:
                    Logger.Error(f"Failed to query current addon info from service after {attempt} attempts.")
                    return (False, None)

                # Query the addonId status.
                r = requests.post('https://homeway.io/api/plugin/info', json={"Id": addonId}, timeout=20)

                Logger.Debug("Homeway Addon info API Result: "+str(r.status_code))
                # If the status code is above 500, retry.
                if r.status_code >= 500:
                    raise Exception(f"Failed call with status code {r.status_code}")

                # Anything else we report as not connected.
                if r.status_code != 200:
                    return (False, None)

                # On success, try to parse the response and see if it's connected.
                jResult = r.json()
                Logger.Debug("Homeway Addon API info; Name:"+jResult["Result"]["Name"] + " HasOwners:" +str(jResult["Result"]["HasOwners"]))

                # Only return the name if there the addon is linked to an account.
                addonName = None
                if jResult["Result"]["HasOwners"] is True:
                    addonName = jResult["Result"]["Name"]
                return (True, addonName)
            except Exception:
                Logger.Warn("Failed to get addon info from service, trying again in just a second...")
                time.sleep(2.0 * attempt)


    def _GetAddAddonUrl(self, addonId):
        return "https://homeway.io/getstarted?id="+addonId
