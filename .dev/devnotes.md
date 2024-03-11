## To Run Locally In A Home Assistant Container

Follow this guide to setup the local docker container and running it.
https://developers.home-assistant.io/docs/add-ons/testing

- Open the project in VS code and the re-open into a dev container
- When open, Ctrl-Shift-P to open the commands, and then type "Run Task"
- Select "Start Home Assistant"
- Go go http://localhost:7123
- In Home Assistant, go to the addons, select install, and you will see Homeway listed as local.
- To update after making changes:
    - bump the version number in the config and comment out the image path.
    - then in HA refresh the addons, go into the Homeway page, and hit update.

## To Run The Dev Host On A Remote Linux Device

- Clone this repo in on debian based OS.
- Open VS Code and remote into the <repo root>/homeway/
- Setup A PY3 virtual environment
    - python3 -m venv py3venv
    - source py3venv/bin/activate
- Install or update the required python libs
    - pip install -r ./homeway/requirements.txt
- Create the dir /home/pi/homeway-store
    - Or edit the dev config for a different path.
- In VS Code, select the "Run And Debug" tab
- Select Linux Host - Dev
- Press F5 to run!


## Editing the Dev Host vars

- Update the vars in the ./vscode/launch.json file.

## For PY 3

- Use `python3 -m venv py3venv` to create an environment in the current dir
- Use `source py3venv/bin/activate` to activate
- Pip install deps from the setup.py file
