import os

from .Context import Context
from .Logging import Logger
from .Util import Util

class Permissions:
    # Must be lower case.
    c_RootUserName = "root"

    def EnsureRunningAsRootOrSudo(self, context:Context) -> None:
        # But regardless of the user, we must have sudo permissions.
        # pylint: disable=no-member # Linux only
        if os.geteuid() != 0:
            if context.Debug:
                Logger.Warn("Not running as root, but ignoring since we are in debug.")
            else:
                raise Exception("Script not ran as root or using sudo. This is required to integrate into Home Assistant.")


    # Called at the end of the setup process, just before the service is restarted or updated.
    # The point of this is to ensure we have permissions set correctly on all of our files,
    # so the addon can access them.
    #
    # We always set the permissions for all of the files we touch, to ensure if something in the setup process
    # did it wrong, a user changed them, or some other service changed them, they are all correct.
    def EnsureFinalPermissions(self, context:Context):

        # A helper to set file permissions.
        # We try to set permissions to all paths and files in the context, some might be null
        # due to the setup mode. We don't care to difference the setup mode here, because the context
        # validation will do that for us already. Thus if a field is None, its ok.
        def SetPermissions(path:str):
            if path is not None and len(path) != 0:
                Util.SetFileOwnerRecursive(path, context.UserName)

        # For all setups, make sure the entire repo is owned by the user who launched the script.
        # This is required, in case the user accidentally used the wrong user at first and some part of the git repo is owned by the root user.
        Util.SetFileOwnerRecursive(context.RepoRootFolder, context.UserName)

        # These following files or folders must be owned by the user the service is running under.
        SetPermissions(context.AddonFolder)
        SetPermissions(context.LogFolder)
        SetPermissions(context.LocalDataFolder)
