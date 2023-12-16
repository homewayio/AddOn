#!/bin/bash

echo "Installing Required Libs"
sudo apt-get update
sudo apt-get install python3 -y
sudo apt-get install python3-pip -y

echo "Installing Python Libs"
pip install -r ../homeway/requirements.txt

# Use sudo, so it installs globally
echo "Installing pylint"
sudo pip install pylint 1> /dev/null 2> /dev/null

echo "Creating folders"
mkdir -p /home/quinn/homeway

if [ ! -d "/mnt/c/Users/quinn/Repos/Homeway.AddOn/" ]; then
  echo "Error! The repo must be checked out to /mnt/c/Users/quinn/Repos/Homeway.AddOn/ or the dev config must be updated to reflect the new location."
  exit 1
fi
