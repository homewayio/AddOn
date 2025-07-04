import logging
import hashlib

from .sentry import Sentry
from .httprequest import HttpRequest
from .streammsgbuilder import StreamMsgBuilder


# A simple class that can handle serving special files that are custom to homeway.
# We use our custom included files to prevent the frontend from breaking when the user is out of data.
class CustomFileServer:

    _HomewayCustomPath = "/homeway/"
    _HomewayCustomIndexJsFileName = "homeway.js"
    _HomewayCustomIndexCssFileName = "homeway.css"

    _Instance = None

    @staticmethod
    def Init(logger):
        CustomFileServer._Instance = CustomFileServer(logger)


    @staticmethod
    def Get():
        return CustomFileServer._Instance


    def __init__(self, logger:logging.Logger):
        self.Logger = logger
        # These are set once we have the addon id and api key.
        self.HomewayCustomHtmlHeaderIncludeBytes = None
        self.HomewayJsFileContentsBytes = None
        self.HomewayCssFileContentsBytes = None


    # This is called once we are connected to Homeway and we know the addon id and api key.
    # NOTE that this API key IS NOT a secret, so it's fine to put it in the JS file.
    def UpdateAddonConfig(self, addonId:str, apiKey:str):
        try:
            # When we get the addon id and api key, we can now generate the custom js file.
            # Insert the addon id and api key into the js file.
            customConfigFile = CustomFileServer._HomewayJsFileContents

            # Sanity check
            addonIdTag = "{{{AddonId}}}"
            addonApiKeyTag = "{{{AddonApi}}}"
            addonIdPos = customConfigFile.find(addonIdTag)
            if addonIdPos == -1:
                self.Logger.error(f"Failed to find {addonIdTag} in Homeway custom js file.")
                return
            addonApiKeyPos = customConfigFile.find(addonApiKeyTag)
            if addonApiKeyPos == -1:
                self.Logger.error(f"Failed to find {addonApiKeyTag} in Homeway custom js file.")
                return
            customConfigFile = customConfigFile.replace(addonIdTag, addonId)
            customConfigFile = customConfigFile.replace(addonApiKeyTag, apiKey)

            # Convert to bytes and hash it, which we use as a tag for caching.
            self.HomewayJsFileContentsBytes = customConfigFile.encode()
            jsFileHash = hashlib.sha256(self.HomewayJsFileContentsBytes).hexdigest()

            # Convert the css to bytes
            self.HomewayCssFileContentsBytes = CustomFileServer._HomewayCssFileContents.encode()
            cssFileHash = hashlib.sha256(self.HomewayCssFileContentsBytes).hexdigest()

            # Build the script tag
            self.HomewayCustomHtmlHeaderIncludeBytes = f'<script src="{CustomFileServer._HomewayCustomPath}{CustomFileServer._HomewayCustomIndexJsFileName}?v={jsFileHash}" defer></script><link rel="stylesheet" href="{CustomFileServer._HomewayCustomPath}{CustomFileServer._HomewayCustomIndexCssFileName}?v={cssFileHash}">'.encode()
        except Exception as e:
            Sentry.Exception("CustomFileServer.UpdateAddonConfig failed.", e)


    # This will return the tag or None if the script isn't ready to be sent yet.
    def GetCustomHtmlHeaderIncludeBytes(self) -> str:
        return self.HomewayCustomHtmlHeaderIncludeBytes


    # Returns True or False depending if this request is for a custom Homeway file or not.
    # If True is returned, HandleRequest should be used to get the response.
    def IsCustomFileRequest(self, httpInitialContext, method:str) -> bool:
        try:
            # It must be a get request.
            if method != "GET":
                return False

            # Read out the path.
            path = StreamMsgBuilder.BytesToString(httpInitialContext.Path())
            if path is None:
                raise Exception("IsCustomFileGet Http request has no path field in IsCustomFileGet.")
            path = HttpRequest.ParseOutPath(path)
            if path is None:
                raise Exception("CustomFileServer.ParseOutPath returned None.")
            path = path.lower()

            # See if the path starts with our special prefix, if it does, we handle it.
            return path.startswith(CustomFileServer._HomewayCustomPath)
        except Exception as e:
            Sentry.Exception("CustomFileServer.IsCustomFileRequest failed.", e)
        return False


    # Must return a HttpRequest.Result object, or None on failure.
    def HandleRequest(self, httpInitialContext) -> HttpRequest.Result:
        # Get the request path.
        pathAndQueryParams = StreamMsgBuilder.BytesToString(httpInitialContext.Path())
        if pathAndQueryParams is None:
            raise Exception("HandleRequest called with no path.")
        path = HttpRequest.ParseOutPath(pathAndQueryParams)
        if path is None:
            raise Exception("CustomFileServer.ParseOutPath returned None.")

        # Match the path to the custom files.
        returnBuffer = None
        headers = {}
        path = path.lower()
        if path.endswith(CustomFileServer._HomewayCustomIndexJsFileName):
            returnBuffer = self.HomewayJsFileContentsBytes
            headers["Content-Type"] = "application/javascript"
        elif path.find(CustomFileServer._HomewayCustomIndexCssFileName) != -1:
            returnBuffer = self.HomewayCssFileContentsBytes
            headers["Content-Type"] = "text/css"
        else:
            raise Exception(f"CustomFileServer.HandleRequest called with an unmatched path: {path}")

        # This shouldn't be possible, since the client can't make requests before the handshake is ready, and the files are made right after that.
        if returnBuffer is None:
            raise Exception("CustomFileServer.HandleRequest called before the custom js file is ready.")
        return HttpRequest.Result(200, headers, pathAndQueryParams, False, fullBodyBuffer=returnBuffer)


    # For now embedding this CSS file here is the easiest way to do it.
    _HomewayCssFileContents = """
.hw-popup {
    max-width:350px;
    z-index:100;

    display:flex;
    flex-direction: column;

    border-radius: 5px;
    color: white;
    font-family: Roboto, sans-serif;
    background-color: #2A2C30;
    box-shadow: 0 3px 1px -2px #0003,0 2px 2px #00000024,0 1px 5px #0000001f;

    position:fixed;
    right:20px;
    top:70px; /* offset the top to be under the title bar */
    margin-left: 20px;
    margin-bottom: 20px;
    visibility: collapse;
}

.hw-popup-title {
    color: white;
    font-size: 20px;
    padding: 12px;
    background-color: #43464F; /* This is the default color, but it can be overwritten by the notification type */
    border-radius: 5px 5px 0px 0px;
}

.hw-popup-msg {
    color: white;
    font-size: 16px;
    margin: 12px;
}

.hw-popup-msg a {
    color: #81a7e5;
}

.hw-popup-msg a:hover {
    color: #a8caff;
}

.hw-popup-button-container {
    display: flex;
    flex-direction: column;
    flex-grow: 1;
    margin: 12px;
    margin-bottom: 0px; /* But button has bottom padding */

    -webkit-user-select: none;
    -ms-user-select: none;
    user-select: none;
}

.hw-popup-button {
    flex-grow: 1;
    text-align: center;
    padding: 6px;
    margin-bottom: 12px;
    font-size: 16px;

    color: white;
    background-color: #3F5682;
    border-radius: 5px;
    transition: 1s;
}

.hw-popup-button:hover {
    background-color: #506da5;
    color: white;
    cursor: pointer;
}

.hw-popup-button-action {
    background-color: #506da5;
}

.hw-popup-button-action:hover {
    background-color: #738ec4;
}
"""

    # For now embedding this JS file here is the easiest way to do it.
    _HomewayJsFileContents = """

//
// Homeway Addon Data Check
//
// This helper script is only added when Home Assistant is loaded via Homeway.
//
// The point of the script is the check if the user hit their data limit, and if so, redirect so they don't sit on a broken frontend.
// The problem is since Home Assistant uses PWA APIs to cache the html locally, the portal will always load, but the APIs will fail if the user hit their data limit.
// That means the user get stuck on a broken page.
// So we call our quick API to see if the user is in this state, and if so, we can redirect them to somewhere with info.
//
// We also can return a notification, like if the user setup Alexa or something and the connection is broken.
//

//
// Global Vars
// Note these vars are injected per client and ARE NOT secrets, so they are safe to put in the JS file.
//
let hw_info_addon_id = "{{{AddonId}}}";
let hw_info_addon_api = "{{{AddonApi}}}";

//
// Logging Helpers
//
var hw_debug_log = false;
function hw_log(msg)
{
    if(!hw_debug_log)
    {
        return;
    }
    console.log("HW INFO: "+msg)
}
function hw_error(msg)
{
    console.log("HW ERROR: "+msg)
}

hw_do_load = function()
{
    //
    // Popup logic
    //
    var c_hwAutoTimeHideDurationSec = 5.0;
    var hwAutoTimeHideSec = 0.0;
    var hwAutoHideTimerHandle = null;
    var hwActionLinkUrl = null;

    // Create the main pop-up
    var hwPopup = document.createElement('div');
    hwPopup.className = 'hw-popup';
    hwPopup.id = 'hw-popup';
    document.body.appendChild(hwPopup);

    // Create the title class
    var popupTitle = document.createElement('div');
    popupTitle.className = "hw-popup-title";
    hwPopup.append(popupTitle);

    // Create the body message.
    var popupMsg = document.createElement('div');
    popupMsg.className = "hw-popup-msg";
    hwPopup.append(popupMsg);

    // Create the close button container
    var popupCloseButtonContainer = document.createElement('div');
    popupCloseButtonContainer.className = "hw-popup-button-container";
    hwPopup.append(popupCloseButtonContainer);

    // Create the close button.
    var popupActionButton = document.createElement('div');
    popupActionButton.classList.add("hw-popup-button");
    popupActionButton.classList.add("hw-popup-button-action");
    popupActionButton.innerHTML = "Learn More"
    popupCloseButtonContainer.append(popupActionButton);

    // Create the close button.
    var popupCloseButton = document.createElement('div');
    popupCloseButton.className = "hw-popup-button";
    popupCloseButton.innerHTML = "Close"
    popupCloseButtonContainer.append(popupCloseButton);

    // Setup the close handler.
    popupCloseButton.addEventListener("click", function(event)
    {
        hw_log("Popup closed clicked.")
        event.preventDefault();
        hw_hide_popup();
    });
    popupActionButton.addEventListener("click", function(event)
    {
        hw_log("Popup action button clicked.")
        event.preventDefault();
        if(hwActionLinkUrl != null)
        {
            window.open(hwActionLinkUrl, "_blank");
        }
        hw_hide_popup();
    });
    hwPopup.addEventListener('mouseover', function()
    {
        hw_log("Popup hovered, stopping timer.")
        hw_clear_auto_hide();
    });
    hwPopup.addEventListener('mouseleave', function()
    {
        hw_log("Popup mouse leave.")
        hw_setup_auto_hide();
    });
    hwPopup.addEventListener('pointerenter', function()
    {
        // For pointer events, the user might touch and not leave again,
        // thus we won't get an exit. In that case, we will just extend the timer,
        // so it still leaves eventually.
        hw_log("Popup pointer entered, extending timer.")
        // Restart the timer and add 5s to whatever time was being used.
        hw_setup_auto_hide(5);
    });

    function hw_hide_popup()
    {
        hw_log("Hiding popup")
        hwPopup.style.opacity = 0;
        hwPopup.style.transition = "0.5s";
        hwPopup.style.transitionTimingFunction = "ease-in";
        hw_clear_auto_hide();
        setTimeout(function(){
            hwPopup.style.visibility = "collapse";
        }, 500);
    }

    function hw_show_popup(title, messageHtml, typeStr, actionText = null, actionLink = null, showForSec = c_hwAutoTimeHideDurationSec)
    {
        hw_log("Showing popup")

        // Set the vars in to the UI.
        popupTitle.innerText = title;
        popupMsg.innerHTML = messageHtml;
        switch(typeStr)
        {
            // Don't use yellow, use default for now.
            // case "notice":
            //     popupTitle.style.backgroundColor = "#4b4838"
            //     break;
            case "success":
                popupTitle.style.backgroundColor = "#3d4b38"
                break;
            case "error":
                popupTitle.style.backgroundColor = "#4b3838"
                break;
            default:
            case "notice":
            case "info":
                popupTitle.style.backgroundColor = "#43464F"
                break;
        }

        // Show or hide the action button, if needed.
        if(actionText != null && actionLink != null && typeof actionText === "string" && typeof actionLink === "string" && actionText.length > 0 && actionLink.length > 0)
        {
            popupActionButton.style.display = "block";
            hwActionLinkUrl = actionLink;
            popupActionButton.innerHTML = actionText;
        }
        else
        {
            popupActionButton.style.display = "none";
            hwActionLinkUrl = null;
        }

        hwPopup.style.visibility = "visible";
        hwPopup.style.opacity = 0;
        hwPopup.style.transitionTimingFunction = "ease-out";
        hwPopup.style.transition = "0s";
        setTimeout(function(){
            hwPopup.style.transition = "0.5s";
            hwPopup.style.opacity = 1;
        }, 50);

        // Setup auto hide
        hwAutoTimeHideSec = showForSec;
        hw_setup_auto_hide();
    }

    // If there should be an auto hide timer, this starts it.
    function hw_setup_auto_hide(extraTimeSec = 0)
    {
        hw_clear_auto_hide();
        if(hwAutoTimeHideSec > 0)
        {
            hw_log("Auto hide enabled for "+hwAutoTimeHideSec)
            hwAutoHideTimerHandle = setTimeout(function()
            {
                hw_hide_popup();
            },
            (hwAutoTimeHideSec * 1000) + extraTimeSec);
        }
    }

    // Stops a timeout if there's one running.
    function hw_clear_auto_hide()
    {
        if(hwAutoHideTimerHandle != null)
        {
            hw_log("Auto hide stopped.")
            clearTimeout(hwAutoHideTimerHandle);
            hwAutoHideTimerHandle = null;
        }
    }

    function DoDataCheck(isUpdateDueToVisibilityChange = false)
    {
        // Create the payload
        var payload = {
            "AddonId": hw_info_addon_id,
            "ApiKey": hw_info_addon_api,
            "CheckDueToVisChange": isUpdateDueToVisibilityChange
        };
        hw_log("Starting data check")
        fetch("https://homeway.io/api/plugin-frontend/checkdatausage",
        {
            credentials: "omit",
            method: "POST",
            headers:
            {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
        })
        .then(response => response.json())
        .then(response =>
        {
            try
            {
                if(response.Status !== 200)
                {
                    hw_error("Failed to call data check api; "+response.Status);
                    return;
                }
                if(response.Result === undefined || response.Result === null)
                {
                    hw_error("Data check call failed, no result.")
                    return;
                }
                hw_log("Data check call success.")

                // If the user is out of data, redirect
                if(response.Result.OutOfData === true)
                {
                    hw_log("User is out of data, redirecting.")
                    // Redirect the page, since it's broken as is.
                    window.location.href = "https://homeway.io/datalimitreached?source=plugin-out-of-data";
                    return;
                }

                // If there's a notification, fire it.
                if(response.Result.Notification !== undefined && response.Result.Notification !== null)
                {
                    hw_log("Data check notification")
                    var note = response.Result.Notification;
                    hw_show_popup(note.Title, note.Message, note.Type, note.ActionText, note.ActionLink, note.ShowForSec);
                }
            }
            catch (error)
            {
                hw_error("Exception in DoNotificationCheckIn "+error)
            }
        })
        .catch((e)=>
        {
            hw_error("Failed to make data check call. "+e)
        })
    }

    // Do the data check on page load, and also when the visibly changes, which helps the mobile app on resume.
    DoDataCheck();
    addEventListener("visibilitychange", (event) => {
        if(document.hidden === false)
        {
            hw_log("Visibility changed, checking data.")
            DoDataCheck(true);
        }
    });
};

// Since we use the async script tag, sometimes we are loaded after the dom is ready, sometimes before.
// If so, do the load work now.
if(document.readyState === 'loading')
{
    hw_log("Deferring load for DOMContentLoaded")
    document.addEventListener('DOMContentLoaded', hw_do_load);
}
else
{
    hw_log("Dom is ready, loading now.")
    hw_do_load()
}
"""
