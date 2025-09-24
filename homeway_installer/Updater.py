import os
import stat

from .Context import Context
from .Logging import Logger
from .Util import Util

# This class contains some logic to make updates easier for the user.
class Updater:

    # This function ensures there's an update script placed in the user's root directory, so it's easy for the user to find
    # the script for updating.
    def PlaceUpdateScriptInRoot(self, context:Context) -> bool:
        try:
            # Create the script file with any optional args we might need.
            s:str = f'''\
#!/bin/bash

#
# This is a helper script to allow easy updating of the Homeway standalone add-on.
# To update your add-on, simply run the script and follow the prompts!
#
# If you need help, feel free to contact us at support@homeway.io
#

# The update and install scripts need to be ran from the repo root.
# So just cd and execute our update script! Easy peasy!
startingDir=$(pwd)
cd {context.RepoRootFolder}
./update.sh
cd $startingDir
            '''
            # Create the file.
            updateFilePath:str = os.path.join(context.UserHomePath, "update-homeway.sh")
            with open(updateFilePath, 'w', encoding="utf-8") as f:
                f.write(s)

            # Make sure to make it executable
            st:os.stat_result = os.stat(updateFilePath)
            os.chmod(updateFilePath, st.st_mode | stat.S_IEXEC)

            # Ensure the user who launched the installer script has permissions to run it.
            Util.SetFileOwnerRecursive(updateFilePath, context.UserName)

            return True
        except Exception as e:
            Logger.Error("Failed to write updater script to user home. "+str(e))
            return False


    # We need to be running as sudo to make a sudo cron job.
    # The cron job has to be sudo, so it can update system packages and restart the service.
    def EnsureCronUpdateJob(self, oeRepoRoot:str) -> None:
        pass
        # This is disabled for now, due to problems running the update script as the root user.
        # try:
        #     Logger.Debug("Ensuring cron job is setup.")

        #     # First, get any current crontab jobs.
        #     # Note it's important to use sudo, because we need to be in the sudo crontab to restart our service!
        #     (returnCode, currentCronJobs, errorOut) = Util.RunShellCommand("sudo crontab -l", False)
        #     # Check for failures.
        #     if returnCode != 0:
        #         # If there are no cron jobs, this will be the output.
        #         if "no crontab for" not in errorOut.lower():
        #             raise Exception("Failed to get current cron jobs. "+errorOut)

        #     # Go through the current cron jobs and try to find our cron job.
        #     # If we find ours, filter it out, since we will re-add an updated one.
        #     currentCronJobLines = currentCronJobs.split("\n")
        #     newCronJobLines = []
        #     for job in currentCronJobLines:
        #         # Skip blank lines
        #         if len(job) == 0:
        #             continue
        #         jobLower = job.lower()
        #         if oeRepoRoot.lower() in jobLower:
        #             Logger.Debug(f"Found our current crontab job: {job}")
        #         else:
        #             Logger.Debug(f"Found other crontab line: {job}")
        #             newCronJobLines.append(job)

        #     # We either didn't have a job or removed it, so add our new job.
        #     # This is our current update time "At 23:59 on Sunday."
        #     # https://crontab.guru/#59_23_*_*_7
        #     # We need to cd into the repo root, since that's where the update script is expected to be ran.
        #     # We send logs out to a file, so we can capture them is needed.
        #     # updateScriptPath = os.path.join(oeRepoRoot, "update.sh")
        #     # This is disabled right now due to issues running as root, but needing to be in the user's context for the install.sh script.
        #     # The problem is we need basically "pi user with the sudo command" but the cron tab runs as the sudo user. In this case, things like the USER and HOME env
        #     # vars aren't defined.
        #     #newCronJobLines.append(f"59 23 * * 7 cd {oeRepoRoot} && {updateScriptPath} 1> /var/log/oe-cron.log 2> /var/log/oe-cron-error.log")

        #     # New output.
        #     newInput = ""
        #     for job in newCronJobLines:
        #         newInput += job + "\n"
        #     Logger.Debug(f"New crontab input: {newInput}")

        #     # Set the new cron jobs.
        #     # Note it's important to use sudo, because we need to be in the sudo crontab to restart our service!
        #     result = subprocess.run("sudo crontab -", check=False, shell=True, capture_output=True, text=True, input=newInput)
        #     if result.returncode != 0:
        #         raise Exception("Failed to set new cron jobs. "+result.stderr)

        #     Logger.Debug("Cron job setup successfully.")
        # except Exception as e:
        #     Logger.Warn("Failed to setup cronjob for updates, skipping. "+str(e))
