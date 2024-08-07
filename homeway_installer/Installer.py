import sys
import traceback

from .Linker import Linker
from .Logging import Logger
from .Service import Service
from .Context import Context
from .Discovery import Discovery
from .Configure import Configure
from .Updater import Updater
from .Permissions import Permissions
from .OptionalDepsInstaller import OptionalDepsInstaller

class Installer:

    def Run(self):
        try:
            # Any error during the process will be thrown, which will be printed here, and exit the installer.
            self._RunInternal()
        except Exception as e:
            tb = traceback.format_exc()
            Logger.Blank()
            Logger.Blank()
            Logger.Error("Installer failed - "+str(e))
            Logger.Blank()
            Logger.Blank()
            Logger.Error("Stack Trace:")
            Logger.Error(str(tb))
            Logger.Blank()
            Logger.Blank()
            Logger.Header("Please contact our support team directly at support@homeway.io so we can help you fix this issue!")
            Logger.Blank()
            Logger.Blank()


    def _RunInternal(self):

        #
        # Setup Phase
        #

        # The installer script passes a json object to us, which contains all of the args.
        # But it might not be the first arg.
        argObjectStr = self.GetArgumentObjectStr()
        if argObjectStr is None:
            raise Exception("Failed to find cmd line json arg")

        # Parse and validate the args.
        context = Context.LoadFromArgString(argObjectStr)

        # As soon as we have the user home make the log file.
        Logger.InitFile(context.UserHomePath, context.UserName)

        # Parse the original CmdLineArgs
        Logger.Debug("Parsing script cmd line args.")
        context.ParseCmdLineArgs()

        # Print this again now that the debug cmd flag is parsed, since it might be useful.
        if context.Debug:
            Logger.Debug("Found config: "+argObjectStr)

        # Validate we have the required args, but not the values yet, since they are optional.
        # All generation 1 vars must exist and be valid.
        Logger.Debug("Validating args")
        context.Validate(1)

        #
        # Run Phase
        #

        # If the help flag is set, do that now and exit.
        if context.ShowHelp:
            # If we should show help, do it now and return.
            self.PrintHelp()
            return

        # Ensure the script at least has sudo permissions.
        # It's required to set file permission and to write / restart the service.
        # See comments in the function for details.
        permissions = Permissions()
        permissions.EnsureRunningAsRootOrSudo(context)

        # Since this runs an async thread, kick it off now so it can start working.
        OptionalDepsInstaller.TryToInstallDepsAsync(context)

        # We will do discovery to see if we find any other existing instances.
        discovery = Discovery()
        discovery.Discovery(context)

        # Validate the response.
        # All generation 2 values must be set and valid.
        if context is None:
            raise Exception("Discovery returned an invalid context.")
        context.Validate(2)

        # Next, based on the vars generated by discovery, complete the configuration of the context.
        configure = Configure()
        configure.Run(context)

        # After configuration, gen 3 should be fully valid.
        context.Validate(3)

        # Before we start the service, check if the secrets config file already exists and if an addon id already exists.
        # This will indicate if this is a fresh install or not.
        context.ExistingAddonId = Linker.GetAddonIdFromServiceSecretsConfigFile(context)

        # Final validation
        context.Validate(4)

        # Just before we start (or restart) the service, ensure all of the permission are set correctly
        permissions.EnsureFinalPermissions(context)

        # If there was an install running, wait for it to finish now, before the service starts.
        # For most installs, the user will take longer to add the info than it takes to install zstandard.
        OptionalDepsInstaller.WaitForInstallToComplete()

        # We are fully configured, create the service file and it's dependent files.
        service = Service()
        service.Install(context)

        # Apply our update logic.
        updater = Updater()
        updater.PlaceUpdateScriptInRoot(context)
        # updater.EnsureCronUpdateJob(context.RepoRootFolder)

        # The service is ready! Now do the account linking process.
        linker = Linker()
        linker.Run(context)

        # Success!
        Logger.Blank()
        Logger.Blank()
        Logger.Purple("              ~~~ Homeway Setup Complete ~~~    ")
        Logger.Warn(  "  You can access your Home Assistant anytime from Homeway.io")
        Logger.Header("                 Welcome To Our Community")
        Logger.Error( "                           <3")
        Logger.Blank()


    def GetArgumentObjectStr(self) -> str:
        # We want to skip arguments until we find the json string and then concat all args after that together.
        # The reason is the PY args logic will split the entire command line string by space, so any spaces in the json get broken
        # up into different args. This only really happens in the case of the CMD_LINE_ARGS, since it can be like "-companion -debug -whatever"
        jsonStr = None
        for arg in sys.argv:
            # Find the json start.
            if len(arg) > 0 and arg[0] == '{':
                jsonStr = arg
            # Once we have started a json string, keep building it.
            elif jsonStr is not None:
                # We need to add the space back to make up for the space removed during the args split.
                jsonStr += " " + arg
        return jsonStr


    def PrintHelp(self):
        Logger.Blank()
        Logger.Blank()
        Logger.Blank()
        Logger.Blank()
        Logger.Header("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        Logger.Header("           Homeway Addon            ")
        Logger.Header("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        Logger.Blank()
        Logger.Info("This installer script is used for installing the Homeway addon for Home Assistant installs that don't support HA addons.")
        Logger.Info("If your Home Assistant supports addons directly, install our Homeway addon instead.")
        Logger.Info("https://homeway.io/getstarted")
        Logger.Blank()
        Logger.Blank()
        Logger.Warn("Optional Args:")
        Logger.Info("  -help            - Shows this message.")
        Logger.Info("  -debug           - Enable debug logging to the console.")
        Logger.Info("  -skipsudoactions - Skips sudo required actions. This is useful for debugging, but will make the install not fully work.")
        Logger.Blank()
        Logger.Info("If you need help, contact our support team at support@homeway.io")
        Logger.Blank()
        Logger.Blank()
