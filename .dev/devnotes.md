
# Add Development

There are a few ways to dev the app, depending on what you're trying to do. They are listed here in the order of the easiest to dev and use.

1) Dev In Companion Mode On A Separate Linux Host
  - This works well for most general dev. The only limits are if you need to dev things that are specifically App features, not just general HA API features.
2) Dev In A Local In A Home Assistant Container
  - This works well if you need to test App specific features, like the config, UI, web portal UI, etc.
3) Dev In A Local HA Server
  - This works well if you need to run the App in the real App mode in HA, but long lived for harder to debug bugs.

## Dev In Companion Mode On A Separate Linux Host

1) Install the Homeway App in companion mode and connect it to HA as normal.
2) Use VS Code to SSH into the host, setup the homeway github repo as the root, us F5 debugging to run the companion.


## Dev In A Local In A Home Assistant Container

Follow this guide to setup the local docker container and running it.
https://developers.home-assistant.io/docs/add-ons/testing

If any of this fails, try pulling the new devcontainer.json and task.json files from:
- https://github.com/home-assistant/devcontainer?tab=readme-ov-file

- Open the project in VS code and the re-open into a dev container
- When open, Ctrl-Shift-P to open the commands, and then type "Run Task"
- Select "Start Home Assistant"
- Go to http://localhost:7123
  - Go to http://localhost:7357/ for the HA supervisor
- Important!
  - You must comment out the `image:` var in the addon config or it will pull the image instead of using local code.
  - If you forget, comment it out, bump the version number, and force an addon update check. Wait until the version number in the HA ui changes.
- In Home Assistant, go to the addons, select install, and you will see Homeway listed as local.
- To update after making changes:
  - Go the addon page in HA
  - Stop the addon
  - Hit rebuild
  - Restart it.


## Dev In A Local HA Server

- Setup Samaba on the HA Server
- Copy the Homeway folder into the add-ons root
- Update the config.yaml
  - Name, slug, ingress_port, comment out the docker image, bump the version
- Go to HA -> Apps -> Install
- Install the dev addon.
- Party!


# Legacy Guides

## To Run The Frontend In Dev

- Clone the frontend repo
- Open in vscode, select run in a dev container
- In the dev container, open the terminal
- Run `yarn install` then `yarn run build`
- When open, Ctrl-Shift-P to open the commands, and then type "Run Task"
- Select "Develop Frontend"
- Now the frontend is ready locally to be used.

- Open the Home Assistant Container core repo in a code dev container
- Add to this `devcontainer.json`

```
"mounts": [
    "source=C:\\Users\\quinn\\Repos,target=/workspaces/repos,type=bind,consistency=cached"
  ]
```

- Add this to the HA `<HA core repo>/config/configuration.yaml`, if the frontend repo folder isn't `frontend`, update it.

```
frontend:
  development_repo: /workspaces/repos/frontend/
```

- In the HA core code window, open the press ctl + shift + p, select `Dev Containers: Rebuild Container`
- Ctrl+Shift+P -> Tasks: Run Task -> Run Home Assistant Core
- Go to `http://localhost:8123/`

ref: https://developers.home-assistant.io/docs/frontend/development/
