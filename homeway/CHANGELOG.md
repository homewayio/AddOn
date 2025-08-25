<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->
<!-- This is used in the homeway UI to show updates, so keep it up to date. -->

## 2.2.12

- 🐞 Bug fixes that should make connectivity better!

## 2.2.10

- 💬 Added support for ElevenLabs voices, which sound amazing!
- 🧠 Major updates to Sage's AI engine - making is way smarter and more powerful!
- 🐛 Fixed a few Sage protocol bugs and worked around a new bug in the latest version of Home Assistant when using text-to-speech.

## 2.2.9

- 🤖 Update to Sage, with more advanced model logic and better location awareness.
- 🐛 Bug fixes to prevent an issue where Sage fails to setup using the Wyoming protocol.

## 2.2 - 2.0

- 🤖 Introducing [Homeway Sage](https://blog.homeway.io/homeway-sage-free-private-intelligent-chatgpt-for-home-assistant-assist-voice/)! Your free, private, and smart Home Assistant Assist!
    - Homeway's Sage assistant uses the latest AI services to empower your Home Assistant Assist for free.
    - Free OpenAI GPT4 chat with extended functions like web search, live sports, live weather, memory, and more.
    - Free text-to-speech powered by OpenAI, Google, Amazon, and more! (you can pick!)
    - Free low-latency speech-to-text powered by the latest AI models.
    - Sage is now available in beta, [follow this guide to help get setup!](https://blog.homeway.io/homeway-sage-free-private-intelligent-chatgpt-for-home-assistant-assist-voice/)

## 1.5.8

- ✨ Adding logic to better handle local access!

## 1.5.7

- 🛠️ Minor update to allow the logging level to be changed for debugging.

## 1.5.6

- 🚀 Adding a new feature to drastically improve streaming API performance!

## 1.5.2-5

- 🪲 Fixing some final stability bugs for launch!

## 1.5.1

- 🚀 Even MORE major CPU and memory performance improvements!
- 🐛 Other minor bug fixes.

## 1.5.0

- 🚀 Major CPU and memory performance improvements! Homeway is now EVEN FASTER!!
- 🐛 Other minor bug fixes.

## 1.4.0-5

- 🐋 Adding a standalone docker image! Using the built in Home Assistant addon is the best option, but for those who can't, they can now use docker!

## 1.3.5-8

- 🏎️ Even more speed improvements!
- 🪲 Fixing a few bugs that cause the Home Assistant frontend to break.

## 1.3.0

- 🏇 We added a new protocol compression library, Zstandard, which makes everything up to 40% faster while using 60% less data!
- 🏎️ Made various protocol optimizations
- 🪲 Fixed various protocol bugs

## 1.2.0

- Standalone addons can now use Alexa and Google Assistant integrations!
- Fixed an issue with assistant proactive state reporting that would cause some reports to fail.

## 1.1.4-6

- Minor bug fix for Alexa and Google state reporting.
- Minor change to bump the addon to server protocol.

## 1.1.3

- Adding the new Homeway icon! 😍
- Fixing an issue where the webserver fails to start.
- Fixing a few small bugs.

## 1.1.0-2

- Performance improvements! Remote access has never been faster!
- Fixed an issue where after linking your account the Homeway addon web portal didn't update.
- Fixed a few bugs around WebSocket lifetime issues.
- Fixed a few issues with Assistants and proactive updates.

## 1.0.6

- Adding support for fast and easy Alexa And Google Assistant support for standalone addons!

## 1.0.4

- Fixing a the SSL handling logic for local addons.

## 1.0.2-3

- Adding logic to support Home Assistant setups that are running only SSL bound websocket ports!

## 1.0.1

- Adding logic to support Home Assistant setups that aren't running on the default 8123 port. The addon will now automatically find the correct port to use!

## 1.0.0

- The official 1.0 Release! 🥳
- Homeway Standalone Add-on - You can now run Homeway directly on your linux device if your Home Assistant setup doesn't support Add-ons!
- Better assistant rate limiting for chatty home devices.

## 0.4.3

- Finishing up device state reporting and device refresh for Alexa and Google Home, meaning when you change your devices they will show up instantly in your apps!

## 0.4.2

- Adding logic for device state reporting to Google Home and Alexa Assistants.

## 0.4.1

 - Address the first beta user feedback! Thank you and keep it coming!

## 0.3.1

- Adding some security hardening to the add-on
- Fixing some websocket protocol issues that prevented some addons from working.

## 0.3.0

- First Beta Build - Now installing from our GitHub package repo!
