#!/bin/bash

#
# Homeway! Free, secure, and private remote access for Home Assistant!
#
# Use this script to install Homeway on your Home Assistant instance if you ARE NOT running an installation of Home Assistant
# that supports add-ons. If you are running an installation of Home Assistant that supports add-ons, use the add-on instead.
#
# Simply run ./install.sh from the git repo root directory to get started!
#
# If you need help, feel free to contact us at support@homeway.io
#





#
# The responsibility of this script is to bootstrap the setup by installing the required system libs,
# virtual environment, and py requirements. The core of the setup logic is done by the PY install script.
#

# Set this to terminate on error.
# We don't do this anymore, because some commands return non-zero exit codes, but still are successful.
# set -e

# Get the root path of the repo, aka, where this script is executing
HA_REPO_DIR=$(readlink -f $(dirname "$0"))

# This is the root of where our py virtual env will be. Note that all Homeway instances share this same
# virtual environment.
HA_ENV="${HOME}/.homeway-env"

# The python requirements are for the installer and plugin
# The virtualenv is for our virtual package env we create
# The curl requirement is for some things in this bootstrap script.
HA_PKGLIST="python3 python3-pip virtualenv curl"

#
# Console Write Helpers
#
c_default=$(echo -en "\e[39m")
c_green=$(echo -en "\e[92m")
c_yellow=$(echo -en "\e[93m")
c_magenta=$(echo -en "\e[35m")
c_red=$(echo -en "\e[91m")
c_cyan=$(echo -en "\e[96m")

log_header()
{
    echo -e "${c_magenta}$1${c_default}"
}

log_important()
{
    echo -e "${c_yellow}$1${c_default}"
}

log_error()
{
    log_blank
    echo -e "${c_red}$1${c_default}"
    log_blank
}

log_info()
{
    echo -e "${c_green}$1${c_default}"
}

log_blue()
{
    echo -e "${c_cyan}$1${c_default}"
}

log_blank()
{
    echo ""
}

#
# Logic to create / update our virtual py env
#
ensure_py_venv()
{
    log_header "Checking Python Virtual Environment For Homeway..."
    # If the service is already running, we can't recreate the virtual env so if it exists, don't try to create it.
    # Note that we check the bin folder exists in the path, since we mkdir the folder below but virtualenv might fail and leave it empty.
    HA_ENV_BIN_PATH="$HA_ENV/bin"
    if [ -d $HA_ENV_BIN_PATH ]; then
        # This virtual env refresh fails on some devices when the service is already running, so skip it for now.
        # This only refreshes the virtual environment package anyways, so it's not super needed.
        #log_info "Virtual environment found, updating to the latest version of python."
        #python3 -m venv --upgrade "${HA_ENV}"
        return 0
    fi

    log_info "No virtual environment found, creating one now."
    mkdir -p "${HA_ENV}"
    virtualenv -p /usr/bin/python3 "${HA_ENV}"
}

#
# Logic to make sure all of our required system packages are installed.
#
install_or_update_system_dependencies()
{
    log_header "Checking required system packages are installed..."
    log_important "You might be asked for your system password - this is required to install the required system packages."

    # It seems a lot of systems don't have the date and time set correctly, and then the fail
    # getting packages and other downstream things. We will will use our HTTP API to set the current UTC time.
    # Note that since cloudflare will auto force http -> https, we use https, but ignore cert errors, that could be
    # caused by an incorrect date.
    log_info "Ensuring the system date and time is correct..."
    sudo date -s `curl --insecure 'https://homeway.io/api/util/date' 2>/dev/null` || true

    # These we require to be installed in the OS.
    # Note we need to do this before we create our virtual environment
    log_info "Installing required system packages..."
    sudo apt update 1>/dev/null` 2>/dev/null` || true
    sudo apt install --yes ${HA_PKGLIST}

    log_info "System package install complete."
}

#
# Logic to install or update the virtual env and all of our required packages.
#
install_or_update_python_env()
{
    # Now, ensure the virtual environment is created.
    ensure_py_venv

    # Update pip if needed
    log_info "Updating PIP if needed... (this can take a few seconds or so)"
    "${HA_ENV}"/bin/python3 -m pip install --upgrade pip

    # Finally, ensure our plugin requirements are installed and updated.
    log_info "Installing or updating required python libs... ${HA_REPO_DIR}"
    "${HA_ENV}"/bin/python3 -m pip install -r "${HA_REPO_DIR}/homeway/requirements.txt"
    log_info "Python libs installed."
}

#
# This Linux addon is not designed to run in a container, including Home Assistant's container.
# Warn the user if a container is detected.
#
check_if_running_in_docker()
{
    if [ -f /.dockerenv ]; then
        log_blank
        log_header       "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
        echo -e "${c_red}                                Warning${c_default}"
        echo -e "${c_red}The Homeway standalone add-on IS NOT designed to be run in a docker container.${c_default}"
        log_header       "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
        log_blank
        log_info      "The Homeway add-on should be set up and installed on the host operating system, not in any docker container."
        log_info      "The add-on most likely WILL NOT WORK if installed into a docker container."
        log_blank
        log_important "To fix this warning, SSH directly into the host OS running on the device run the setup command again."
        log_blank
        log_info      "If you know what you're doing, you can continue the install."
        read -p       "Continue the install? [y/N]: " -e -i "N" cont
        echo ""
        if [ "${cont^^}" != "Y" ] ; then
            exit 0
        fi
    fi
}

log_blank
log_blank
log_blank
cat << EOF
                           ((
                  ##########((((((((((
              ################((((((((((((
           (####################(((((((((((((
         ((################..#####(....(((((((%
        ((##############........##.....((((((((%
       (((###########.....####.........((((((((%%
      (((#########.....##......##......((((((((%%%
      ((#######.....##............##...,,((((((%%%
     ((#####.....##..................##,,,,,(((%%%
      ###########.....................,######((%%%
      ###########....................,,########%%%
       ##########........&&&&&&.....,,,########%%
        #########........&&&&&&...,,,,,#######%%
         ########........&&&&&&.,,,,,,,######%%
           #################################%
              ############################
                  ####################
EOF
log_blank
log_header    "                    ~~ Homeway.io ~~"
log_blank
log_important "Homeway empowers the Home Assistant community with:"
log_info      "  - Free Home Assistant Remote Access"
log_info      "  - Free ChatGPT Powered AI For Home Assistant Assist and Voice Devices"
log_info      "  - Free Official Home Assistant iOS & Android App Remote Access"
log_info      "  - Alexa And Google Assistant Integration"
log_info      "  - Shared Remote Access"
log_info      "  - And More!"
log_blank


# Do our docker warning check
check_if_running_in_docker

# Make sure our required system packages are installed.
# These are required for other actions in this script, so it must be done first.
install_or_update_system_dependencies

# Now make sure the virtual env exists, is updated, and all of our currently required PY packages are updated.
install_or_update_python_env

# Before launching our PY script, set any vars it needs to know
# Pass all of the command line args, so they can be handled by the PY script.
# Note that USER can be empty string on some systems when running as root. This is fixed in the PY installer.
USERNAME=${USER}
USER_HOME=${HOME}
CMD_LINE_ARGS=${@}
PY_LAUNCH_JSON="{\"HA_REPO_DIR\":\"${HA_REPO_DIR}\",\"HA_ENV\":\"${HA_ENV}\",\"USERNAME\":\"${USERNAME}\",\"USER_HOME\":\"${USER_HOME}\",\"CMD_LINE_ARGS\":\"${CMD_LINE_ARGS}\"}"
log_info "Bootstrap done. Starting python installer..."

# Now launch into our py setup script, that does everything else required.
# Since we use a module for file includes, we need to set the path to the root of the module
# so python will find it.
export PYTHONPATH="${HA_REPO_DIR}"

# Move to the installer dir.
CURRENT_DIR=${pwd}
cd ${HA_REPO_DIR} > /dev/null

# Disable the PY cache files (-B), since they will be written as sudo, since that's what we launch the PY
# installer as. The PY installer must be sudo to write the service files, but we don't want the
# complied files to stay in the repo with sudo permissions.
sudo ${HA_ENV}/bin/python3 -B -m homeway_installer ${PY_LAUNCH_JSON}

# Move back to the original dir.
cd ${CURRENT_DIR} > /dev/null

# Check the output of the py script.
retVal=$?
if [ $retVal -ne 0 ]; then
    log_error "Failed to complete setup. Error Code: ${retVal}"
fi

# Note the rest of the user flow (and terminal info) is done by the PY script, so we don't need to report anything else.
exit $retVal