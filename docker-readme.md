# Homeway

Secure, private, and free remote access to your Home Assistant from anywhere! Including [Amazon Alexa](https://homeway.io/alexa?source=github_docker_readme), [Google Assistant](https://homeway.io/googleassistant?source=github_docker_readme), and [Official Home Assistant App](https://homeway.io/app?source=github_docker_readme) support. Developed for the Home Assistant community, by the Home Assistant community.

There are three ways you can setup Homeway:

1) [Use Home Assistant's build-in add-on model.](https://homeway.io/getstarted?source=github_docker_readme&addon=true) - Recommended
2) [Use the standalone docker image.](https://homeway.io/getstarted?source=github_docker_readme&cli=true)
3) [Use the standalone Linux CLI.](https://homeway.io/getstarted?source=github_docker_readme&docker=true)

**If your Home Assistant server supports add-ons, installing the Homeway add-on is recommended for easy and full functionality. [Follow this link for a step-by-step Homeway add-on setup guide.](https://homeway.io/getstarted?source=github_docker_readme&addon=true)**


## Homeway Standalone

If you can't use the HA add-on, the standalone Docker image and Linux CLI work just as well!

The Docker image and Linux CLI can be installed on any computer on the same local LAN as your Home Assistant server. The CLI can be installed on any Debian-based Linux OS. The Docker image can be installed anywhere Docker is supported!

[Follow this link for a step-by-step Linux CLI setup guide.](https://homeway.io/getstarted?source=github_docker_readme&cli=true)


# Homeway Standalone Docker Image Setup

To get the official Homeway Docker image setup and running, just follow the steps below!

Official Standalone Docker Image: https://hub.docker.com/r/homewayio/homeway


## Docker Required Setup Information

To use the Homeway Standalone Docker Container, you need to get the following information:

- Your Home Assistant's server IP or hostname.
- A Long-Lived Access Token from your Home Assistant server.

To create a Long-Lived Access Token in Home Assistant, follow these steps:

1. Open the Home Assistant web UI and go to your user profile:
    - Something like: http://homeassistant.local:8123/profile
2. On your profile page, click the "Security" tab at the top.
3. Scroll down to the bottom, to the "Long-lived access tokens" box.
4. Click "CREATE TOKEN"
5. Enter any name, something "Homeway Addon" works just fine.
6. Copy the access token and use it in the docker run command line or your docker compose file.


## Linking Your Homeway Standalone Docker Addon

Once the docker container is running, use the logs to find the linking URL:

Docker Compose:

`docker compose logs | grep https://homeway.io/getstarted`

Docker:

`docker logs homeway | grep https://homeway.io/getstarted`


## Run The Docker Image

### Using Docker Compose

Docker compose is the easiest way to run the Homeway standalone docker addon.

- Install [Docker and Docker Compose](https://docs.docker.com/compose/install/linux/)
- Clone this repo
- Edit the `./docker-compose.yml` file to enter your environment information.
- Run `docker compose up -d`
- Follow the "Linking Your Homeway Standalone Docker Addon" steps above to link the addon to your account.


### Using Docker

When you first run the docker container, these two values must be set at environment vars. Once the container is run, you only need to include them if you want to update the values.

- HOME_ASSISTANT_IP=(IP or hostname of your Home Assistant server)
- HOME_ASSISTANT_ACCESS_TOKEN=(A long-lived access token from your Home Assistant server)
- Optional:
    - HOME_ASSISTANT_PORT=(Port of your Home Assistant server)
        - Defaults to `8123`

Run the docker container passing the required information:

`docker run --name homeway -e HOME_ASSISTANT_IP=localhost -e HOME_ASSISTANT_ACCESS_TOKEN=token -v /data:/data -d homewayio/homeway`

Follow the "Linking Your Homeway Standalone Docker Addon" steps above to link the addon to your account.


## Building The Image Locally

You can build the docker image locally if you prefer; use the following command.

`docker build -t homeway-local .`