#!/bin/bash

#
# Homeway Addon
#
# This script allows easy updating of the Homeway standalone addon when installed directly on a linux device.
# If you're using a Homeway addon installed via Home Assistant's addon system, this is not required.
#
# If you need help, feel free to contact us at support@homway.io
#

c_default=$(echo -en "\e[39m")
c_green=$(echo -en "\e[92m")
c_yellow=$(echo -en "\e[93m")
c_magenta=$(echo -en "\e[35m")
c_red=$(echo -en "\e[91m")
c_cyan=$(echo -en "\e[96m")

echo ""
echo ""
echo -e "${c_magenta}Starting the Homeway standalone add-on update!${c_default}"
echo ""
echo ""

# Since our cron update script runs as root and git commands have to be ran by the owner,
# when we run the git commands, we need to make sure we are the right user.
runAsRepoOwner()
{
    updateScriptOwner=$(stat -c %U update.sh)
    if [[ $(whoami) == *${updateScriptOwner}* ]]; then
        eval $1
    else
        repoDir=$(realpath $(dirname "$0"))
        sudo su - ${updateScriptOwner} -c "cd ${repoDir} && $1"
    fi
}

# Pull the repo to get the top of main.
echo "Updating repo and fetching the latest released tag..."
runAsRepoOwner "git fetch --tags"

# Find the latest tag, just for stats now.
latestTaggedCommit=$(runAsRepoOwner "git rev-list --tags --max-count=1")
latestTag=$(runAsRepoOwner "git describe --tags ${latestTaggedCommit}")
currentGitStatus=$(runAsRepoOwner "git describe")
echo "Latest git tag found ${latestTag}, current status ${currentGitStatus}"

# Reset any local changes and pull the head of main.
runAsRepoOwner "git reset --hard --quiet"
runAsRepoOwner "git checkout main --quiet"
runAsRepoOwner "git pull --quiet"

# Our installer script has all of the logic to update system deps, py deps, and the py environment.
# So we use it with a special flag to do updating.
echo "Running the update..."
./install.sh -update