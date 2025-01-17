import platform
import logging

import requests

from .mdns import MDns
from .compat import Compat
from .localip import LocalIpHelper
from .httpsessions import HttpSessions
from .streammsgbuilder import StreamMsgBuilder

from .Proto.PathTypes import PathTypes
from .Proto.DataCompression import DataCompression
from .Proto.HaApiTarget import HaApiTarget
from .Proto.HttpInitialContext import HttpInitialContext

class HttpRequest:

    # These are the defaults, they should point directly to the HA server on most setups.
    DirectServicePort = 8123
    DirectServiceAddress = "127.0.0.1"
    DirectServiceIsHttps = False

    # As a fallback, we will try different http proxy options.
    LocalHttpProxyPort = 80
    LocalHttpProxyIsHttps = False


    @staticmethod
    def SetLocalHttpProxyPort(port):
        HttpRequest.LocalHttpProxyPort = port
    @staticmethod
    def GetLocalHttpProxyPort():
        return HttpRequest.LocalHttpProxyPort

    @staticmethod
    def SetLocalHttpProxyIsHttps(isHttps):
        HttpRequest.LocalHttpProxyIsHttps = isHttps
    @staticmethod
    def GetLocalHttpProxyIsHttps():
        return HttpRequest.LocalHttpProxyIsHttps

    @staticmethod
    def SetDirectServicePort(port):
        HttpRequest.DirectServicePort = port
    @staticmethod
    def GetDirectServicePort():
        return HttpRequest.DirectServicePort

    @staticmethod
    def SetDirectServiceAddress(address):
        HttpRequest.DirectServiceAddress = address
    @staticmethod
    def GetDirectServiceAddress():
        return HttpRequest.DirectServiceAddress

    @staticmethod
    def SetDirectServiceUseHttps(address):
        HttpRequest.DirectServiceIsHttps = address
    @staticmethod
    def GetDirectServiceUseHttps():
        return HttpRequest.DirectServiceIsHttps


    # Based on the URL passed, this will return PathTypes.Relative or PathTypes.Absolute
    @staticmethod
    def GetPathType(url):
        if url.find("://") != -1:
            # If there is a protocol, it's for sure absolute.
            return PathTypes.Absolute
        # TODO - It might be worth to add some logic to try to detect no protocol hostnames, like test.com/helloworld.
        return PathTypes.Relative


    # Given a relative URL or absolute URL, returns the path with no query string.
    # Can throw if there's a string issue.
    @staticmethod
    def ParseOutPath(uri:str) -> str:
        # Start the path at 0, assuming a relative URL.
        pathStart = 0
        protocolEnd = uri.find("://")
        if protocolEnd != -1:
            # If there's a protocol start, parse to the end of the hostname and possible port.
            pathStart = uri.find("/", protocolEnd+3)
            # If this is an absolute URL with no path, return a /.
            if pathStart == -1:
                return "/"
        # Remove the query string, if there is one.
        queryStart = uri.find("?", pathStart)
        if queryStart == -1:
            queryStart = len(uri)
        return uri[pathStart:queryStart]


    # This result class is a wrapper around the requests PY lib Response object.
    # For the most part, it should abstract away what's needed from the Response object, so that an actual Response object isn't needed
    # for all http calls. However, sometimes the actual Response object might be in this result object, because a ref to it needs to be held
    # so the body stream can be read, assuming there's no full body buffer.
    #
    # There are three ways this class can contain a body to be used.
    #       1) ResponseForBodyRead - If this is not None, then there's a requests.Response attached to this Result and it can be used to be read from.
    #              Note, in this case, ideally the Result is used with a `with` keyword to cleanup when it's done.
    #       2) FullBodyBuffer - If this is not None, then there's a fully read body buffer that should be used.
    #              In this case, the size of the body is known, it's the size of the full body buffer. The size can't change.
    #       3) CustomBodyStream - If this is not None, then there's a custom body stream that should be used.
    #              This callback can be implemented by anything. The size is unknown and should continue until the callback returns None.
    #                   customBodyStreamCallback() -> byteArray : Called to get more bytes. If None is returned, the stream is done.
    #                   customBodyStreamClosedCallback() -> None : MUST BE CALLED when this Result object is closed, to clean up the stream.
    class Result():
        def __init__(self, statusCode:int, headers:dict, url:str, didFallback:bool, fullBodyBuffer=None, requestLibResponseObj:requests.Response=None, customBodyStreamCallback=None, customBodyStreamClosedCallback=None):
            # Status code isn't a property because some things need to set it externally to the class. (Result.StatusCode = 302)
            self.StatusCode = statusCode
            self._headers = headers
            self._url:str = url
            self._requestLibResponseObj = requestLibResponseObj
            self._didFallback:bool = didFallback
            self._fullBodyBuffer = fullBodyBuffer
            self._bodyCompressionType = DataCompression.None_
            self._fullBodyBufferPreCompressedSize:int = 0
            self.SetFullBodyBuffer(fullBodyBuffer)
            self._customBodyStreamCallback = customBodyStreamCallback
            self._customBodyStreamClosedCallback = customBodyStreamClosedCallback
            if (self._customBodyStreamCallback is not None and self._customBodyStreamClosedCallback is None) or (self._customBodyStreamCallback is None and self._customBodyStreamClosedCallback is not None):
                raise Exception("Both the customBodyStreamCallback and customBodyStreamClosedCallback must be set!")

        @property
        def Headers(self) -> dict:
            return self._headers

        @property
        def Url(self) -> str:
            return self._url

        @property
        def DidFallback(self) -> bool:
            return self._didFallback

        # This should only be used for reading the http stream body and it might be None
        # If this Result was created without one.
        @property
        def ResponseForBodyRead(self) -> requests.Response:
            return self._requestLibResponseObj

        @property
        def FullBodyBuffer(self) -> bytearray:
            # Defaults to None
            return self._fullBodyBuffer

        @property
        def BodyBufferCompressionType(self) -> DataCompression:
            # Defaults to None
            return self._bodyCompressionType

        @property
        def BodyBufferPreCompressSize(self) -> int:
            # There must be a buffer
            if self._fullBodyBuffer is None:
                return 0
            return self._fullBodyBufferPreCompressedSize

        # Note the buffer can be bytes or bytearray object!
        # A bytes object is more efficient, but bytearray can be edited.
        def SetFullBodyBuffer(self, buffer, compressionType:DataCompression = DataCompression.None_, preCompressedSize:int = 0):
            self._fullBodyBuffer = buffer
            self._bodyCompressionType = compressionType
            self._fullBodyBufferPreCompressedSize = preCompressedSize
            if compressionType != DataCompression.None_ and (preCompressedSize == 0 or len(buffer) == 0):
                raise Exception("The pre-compress size or the buffer size can't be zero if compression type is set.")
            if compressionType != DataCompression.None_ and preCompressedSize <= 0:
                raise Exception("The pre-compression full size must be set if the buffer is compressed.")

        # It's important we clear all of the vars that are set above.
        # This is used by the system that updates the request object with a 304 if the cache headers match.
        def ClearFullBodyBuffer(self):
            self._fullBodyBuffer = None
            self._bodyCompressionType = DataCompression.None_
            self._fullBodyBufferPreCompressedSize = 0

        # Since most things use request Stream=True, this is a helpful util that will read the entire
        # content of a request and return it. Note if the request has no defined length, this will read
        # as long as the stream will go.
        # This function will not throw on failures, it will read as much as it can and then set the buffer.
        # On a complete failure, the buffer will be set to None, so that should be checked.
        def ReadAllContentFromStreamResponse(self, logger:logging.Logger) -> None:
            # Ensure we have a stream to read.
            if self._requestLibResponseObj is None:
                raise Exception("ReadAllContentFromStreamResponse was called on a result with no request lib Response object.")
            buffer = None

            # In the past, we used iter_content, but it has a lot of overhead and also doesn't read all available data, it will only read a chunk if the transfer encoding is chunked.
            # This isn't great because it's slow and also we don't need to reach each chunk, process it, just to dump it in a buffer and read another.
            #
            # For more comments, read doBodyRead, but using read is way more efficient.
            # The only other thing to note is that read will allocate the full buffer size passed, even if only some of it is filled.
            try:
                # Ideally we use the content size, but if we can't we use our default.
                perReadSizeBytes = 490 * 1024
                contentLengthStr = self._requestLibResponseObj.headers.get("Content-Length", None)
                if contentLengthStr is not None:
                    perReadSizeBytes = int(contentLengthStr)

                while True:
                    # Read data
                    data = self._requestLibResponseObj.raw.read(perReadSizeBytes)

                    # Check if we are done.
                    if data is None or len(data) == 0:
                        # This is weird, but there can be lingering data in response.content, so add that if there is any.
                        # See doBodyRead for more details.
                        if len(self._requestLibResponseObj.content) > 0:
                            buffer += self._requestLibResponseObj.content
                        # Break out when we are done.
                        break

                    # If we aren't done, append the buffer.
                    if buffer is None:
                        buffer = data
                    else:
                        buffer += data
            except Exception as e:
                lengthStr =  "[buffer is None]" if buffer is None else str(len(buffer))
                logger.warn(f"ReadAllContentFromStreamResponse got an exception. We will return the current buffer length of {lengthStr}, exception: {e}")
            self.SetFullBodyBuffer(buffer)

        @property
        def GetCustomBodyStreamCallback(self):
            return self._customBodyStreamCallback

        @property
        def GetCustomBodyStreamClosedCallback(self):
            return self._customBodyStreamClosedCallback

        # We need to support the with keyword incase we have an actual Response object.
        def __enter__(self):
            if self._requestLibResponseObj is not None:
                self._requestLibResponseObj.__enter__()
            return self

        # We need to support the with keyword incase we have an actual Response object.
        def __exit__(self, t, v, tb):
            if self._requestLibResponseObj is not None:
                self._requestLibResponseObj.__exit__(t, v, tb)
            if self._customBodyStreamClosedCallback is not None:
                self._customBodyStreamClosedCallback()


    # Handles making all http calls out of the plugin to locally ports, other services running locally on the device, or
    # even on other devices on the LAN.
    #
    # The main point of this function is to abstract away the logic around relative paths, absolute URLs, and the fallback logic
    # we use for different ports. See the comments in the function for details.
    @staticmethod
    def MakeHttpCallStreamHelper(logger:logging.Logger, httpInitialContext:HttpInitialContext, method:str, headers, data=None) -> "HttpRequest.Result":
        # Get the vars we need from the stream initial context.
        path = StreamMsgBuilder.BytesToString(httpInitialContext.Path())
        if path is None:
            raise Exception("Http request has no path field in open message.")
        pathType = httpInitialContext.PathType()

        # Make the common call.
        return HttpRequest.MakeHttpCall(logger, path, pathType, method, headers, data)


    # allowRedirects should be false for all proxy calls. If it's true, then the content returned might be from a redirected URL and the actual URL will be incorrect.
    # Instead, the system needs to handle the redirect 301 or 302 call as normal, sending it back to the caller, and allowing them to follow the redirect if needed.
    # The X-Forwarded-Host header will tell the local server the correct place to set the location redirect header.
    # However, for calls that aren't proxy calls, things like local snapshot requests and such, we want to allow redirects to be more robust.
    @staticmethod
    def MakeHttpCall(logger, pathOrUrl, pathOrUrlType, method, headers:dict=None, data=None, allowRedirects=False, apiTarget:HaApiTarget=None) -> "HttpRequest.Result":

        # Handle special API type targets.
        if apiTarget is not None and apiTarget == HaApiTarget.Core:
            # We need to get the access token and the correct server path, depending on if we are running in the addon container or not.
            serverInfoHandler = Compat.GetServerInfoHandler()
            if serverInfoHandler is None:
                raise Exception("A HA core api targeted call was made, but we had no server info handler.")

            # We need to get the access token to talk directly to Home Assistant.
            accessToken = serverInfoHandler.GetAccessToken()
            if accessToken is None or len(accessToken) == 0:
                # Report an error and fall though, the call will be made but with no auth token appended.
                logger.error("A HA core api targeted call was made, but we don't have an access token.")
                apiTarget = None
            else:
                # Add the special auth header with the access token.
                if headers is None:
                    headers = {}
                headers["Authorization"] = f"Bearer {accessToken}"

                # Rewrite the path, which is dependent on if we are running in the addon container or standalone.
                pathOrUrl = serverInfoHandler.GetServerBaseUrl("http") + pathOrUrl
                pathOrUrlType = PathTypes.Absolute

        # Next we need to figure out what the URL is. There are two options
        #
        # 1) Absolute URLs
        # These are the easiest, because we just want to make a request to exactly what the absolute URL is. These are used
        # when the local service is trying to make an local LAN http request to the same device or even a different device.
        # For these to work properly on a remote browser, the Homeway service will detect and convert the URLs in to encoded relative
        # URLs for the portal. This ensures when the remote browser tries to access the HTTP endpoint, it will hit Homeway. The Homeway
        # server detects the special relative URL, decodes the absolute URL, and sends that in the Message as "AbsUrl". For these URLs we just try
        # to hit them and we take whatever we get, we don't care if fails or not.
        #
        # 2) Relative Urls
        # These Urls are the most common, standard URLs. The browser makes the relative requests to the same hostname:port as it's currently
        # on. However, for our setup its a little more complex. The issue is the Homeway plugin not knowing how the user's system is setup.
        # The plugin can with 100% certainty query and know the port local http server is running on directly. So we do that to know exactly what
        # local server to talk to. (consider there might be multiple instances running on one device.)
        #
        # But, the other most common use case for http calls are the webcam streams to mjpegstreamer. This is the tricky part. There are two ways it can be
        # setup. 1) the webcam stream uses an absolute local LAN url with the ip and port. This is covered by the absolute URL system above. 2) The webcam stream
        # uses a relative URL and haproxy handles detecting the webcam path to send it to the proper mjpegstreamer instance. This is the tricky one, because we can't
        # directly query or know what the correct port for haproxy or mjpegstreamer is. We could look at the configs, but a user might not setup the configs in the
        # standard places. So to fix the issue, we use logic in the frontend JS to determine if a web browser is connecting locally, and if so what the port is. That gives
        # use a reliable way to know what port haproxy is running on. It sends that to the plugin, which is then given here as `localHttpProxyPort`.
        #
        # The last problem is knowing which calls should be sent to the local service directly and which should be sent to haproxy. We can't rely on any URL matching, because
        # the user can setup the webcam stream to start with anything they want. So the method we use right now is to simply always request to the local service first, and if we
        # get a 404 back try the haproxy. This adds a little bit of unneeded overhead, but it works really well to cover all of the cases.

        # Setup the protocol we need to use for the direct and http proxy. We need to use the same protocol that was detected.
        directServiceProtocol = "http://"
        if HttpRequest.DirectServiceIsHttps:
            directServiceProtocol = "https://"
        httpProxyProtocol = "http://"
        if HttpRequest.LocalHttpProxyIsHttps:
            httpProxyProtocol = "https://"

        # Figure out the main and fallback url.
        url = ""
        fallbackUrl = None
        fallbackWebcamUrl = None
        fallbackLocalIpDirectServicePortSuffix = None
        fallbackLocalIpHttpProxySuffix = None
        if pathOrUrlType == PathTypes.Relative:

            # Note!
            # These URLs are very closely related to the logic in the WebStreamWsHelper class and should stay in sync!

            # The main URL is directly to this local instance
            url = directServiceProtocol + HttpRequest.DirectServiceAddress + ":" + str(HttpRequest.DirectServicePort) + pathOrUrl

            # The fallback URL is to where we think the http proxy port is.
            # For this address, we need set the protocol correctly depending if the client detected https
            # or not.
            fallbackUrl = httpProxyProtocol + HttpRequest.DirectServiceAddress + ":" +str(HttpRequest.LocalHttpProxyPort) + pathOrUrl

            # If the two URLs above don't work, we will try to call the server using the local IP since the server might not be bound to localhost.
            # Note we only build the suffix part of the string here, because we don't want to do the local IP detection if we don't have to.
            fallbackLocalIpDirectServicePortSuffix = ":" + str(HttpRequest.DirectServicePort) + pathOrUrl
            fallbackLocalIpHttpProxySuffix =  ":" + str(HttpRequest.LocalHttpProxyPort) + pathOrUrl

            # If all else fails, and because this logic isn't perfect, yet, we will also try to fallback to the assumed webcam port.
            # This isn't a great thing though, because more complex webcam setups use different ports and more than one instance.
            # Only setup this URL if the path starts with /webcam, which again isn't a great indicator because it can change per user.
            webcamUrlIndicator = "/webcam"
            pathLower = pathOrUrl.lower()
            if pathLower.startswith(webcamUrlIndicator):
                # We need to remove the /webcam* since we are trying to talk directly to mjpg-streamer
                # We do want to keep the second / though.
                secondSlash = pathOrUrl.find("/", 1)
                if secondSlash != -1:
                    webcamPath = pathOrUrl[secondSlash:]
                    fallbackWebcamUrl = "http://" + HttpRequest.DirectServiceAddress + ":8080" + webcamPath

        elif pathOrUrlType == PathTypes.Absolute:
            # For absolute URLs, only use the main URL and set it be exactly what was requested.
            url = pathOrUrl

            # The only exception to this is for mdns local domains. So here's the hard part. On most systems, mdns works for the
            # requests lib and everything will work. However, on some systems mDNS isn't support and the call will fail. On top of that, mDNS
            # is super flakey, and it will randomly stop working often. For both of those reasons, we will check if we find a local address, and try
            # to resolve it manually. Our logic has a cache and local disk backup, so if mDNS is being flakey, our logic will recover it.
            # TODO - This could break servers that need the hostname to use the right service - but the fallback should cover it.
            localResolvedUrl = MDns.Get().TryToResolveIfLocalHostnameFound(url)
            if localResolvedUrl is not None:
                # The function will only return back the full URL if a local hostname was found and it was able to resolve to an IP.
                # In this case, use our local IP result first, and then set the requested as the fallback.
                # This should be better, because it will use our already resolved IP url first, and if for some reason it fails, we still try the
                # OG URL.
                fallbackUrl = url
                url = localResolvedUrl
        else:
            raise Exception("Http request got a message with an unknown path type. "+str(pathOrUrlType))

        # Ensure if there's no data we don't set it. Sometimes our json message parsing will leave an empty
        # bytearray where it should be None.
        if data is not None and len(data) == 0:
            data = None

        # All of the users of MakeHttpCall don't handle compressed responses.
        # For Stream request, this header is already set in GatherRequestHeaders, but for things like webcam snapshot requests and such, it's not set.
        # Beyond nothing handling compressed responses, since the call is almost always over localhost, there's no point in doing compression, since it mainly just helps in transmit less data.
        # Thus, for all calls, we set the Accept-Encoding to identity, telling the server no response compression is allowed.
        # This is important for somethings like camera-streamer, which will use gzip by default. (which is also silly, because it's sending jpegs and jmpeg streams?)
        if headers is None:
            headers = {}
        headers["Accept-Encoding"] = "identity"

        # First, try the main URL.
        # For the first main url, we set the main response to None and is fallback to False.
        ret = HttpRequest.MakeHttpCallAttempt(logger, "Main request", method, url, headers, data, None, False, fallbackUrl, allowRedirects)
        # If the function reports the chain is done, the next fallback URL is invalid and we should always return
        # whatever is in the Response, even if it's None.
        if ret.IsChainDone:
            return ret.Result

        # We keep track of the main response, if all future fallbacks fail. (This can be None)
        mainResult = ret.Result

        # Main failed, try the fallback, which should be the http proxy.
        ret = HttpRequest.MakeHttpCallAttempt(logger, "Http proxy fallback", method, fallbackUrl, headers, data, mainResult, True, fallbackLocalIpHttpProxySuffix, allowRedirects)
        # If the function reports the chain is done, the next fallback URL is invalid and we should always return
        # whatever is in the Response, even if it's None.
        if ret.IsChainDone:
            return ret.Result

        # Try to get the local IP of this device and try to use the same ports with it.
        # We build these full URLs after the failures so we don't try to get the local IP on every call.
        localIp = LocalIpHelper.TryToGetLocalIp()

        # With the local IP, first try to use the http proxy URL, since it's the most likely to be bound to the public IP and not firewalled.
        # It's important we use the right http proxy protocol with the http proxy port.
        localIpFallbackUrl = httpProxyProtocol + localIp + fallbackLocalIpHttpProxySuffix
        ret = HttpRequest.MakeHttpCallAttempt(logger, "Local IP Http Proxy Fallback", method, localIpFallbackUrl, headers, data, mainResult, True, fallbackLocalIpDirectServicePortSuffix, allowRedirects)
        # If the function reports the chain is done, the next fallback URL is invalid and we should always return
        # whatever is in the Response, even if it's None.
        if ret.IsChainDone:
            return ret.Result

        # Now try the local service direct port with the local IP.
        localIpFallbackUrl = "http://" + localIp + fallbackLocalIpDirectServicePortSuffix
        ret = HttpRequest.MakeHttpCallAttempt(logger, "Local IP fallback", method, localIpFallbackUrl, headers, data, mainResult, True, fallbackWebcamUrl, allowRedirects)
        # If the function reports the chain is done, the next fallback URL is invalid and we should always return
        # whatever is in the Response, even if it's None.
        if ret.IsChainDone:
            return ret.Result

        # If all others fail, try the hardcoded webcam URL.
        # Note this has to be last, because there commonly isn't a fallbackWebcamUrl, so it will stop the
        # chain of other attempts.
        ret = HttpRequest.MakeHttpCallAttempt(logger, "Webcam hardcode fallback", method, fallbackWebcamUrl, headers, data, mainResult, True, None, allowRedirects)
        # No matter what, always return the result now.
        return ret.Result

    # Returned by a single http request attempt.
    # IsChainDone - indicates if the fallback chain is done and the response should be returned
    # Result - is the final result. Note the result can be unsuccessful or even `None` if everything failed.
    class AttemptResult():
        def __init__(self, isChainDone, result):
            self.isChainDone = isChainDone
            self.result:HttpRequest.Result = result

        @property
        def IsChainDone(self):
            return self.isChainDone

        @property
        def Result(self):
            return self.result

    # This function should always return a AttemptResult object.
    @staticmethod
    def MakeHttpCallAttempt(logger, attemptName, method, url, headers, data, mainResult, isFallback, nextFallbackUrl, allowRedirects:bool = False) -> Result:
        response = None
        try:
            # Try to make the http call.
            #
            # Note we use a long timeout because some api calls can hang for a while.
            # For example when plugins are installed, some have to compile which can take some time.
            # timeout note! This value also effects how long a body read can be. This can effect unknown body chunk stream reads can hang while waiting on a chunk.
            # But whatever this timeout value is will be the max time a body read can take, and then the chunk will fail and the stream will close.
            #
            # See the note about allowRedirects above MakeHttpCall.
            #
            # It's important to set the `verify` = False, since if the server is using SSL it's probably a self-signed cert. Or it's a cert for a hostname we aren't using.
            #
            # We always set stream=True because we use the iter_content function to read the content.
            # This means that response.content will not be valid and we will always use the iter_content. But it also means
            # iter_content will ready into memory on demand and throw when the stream is consumed. This is important, because
            # our logic relies on the exception when the stream is consumed to end the http response stream.
            response = HttpSessions.GetSession(url).request(method, url, headers=headers, data=data, timeout=1800, allow_redirects=allowRedirects, stream=True, verify=False)
        except Exception as e:
            logger.info(attemptName + " http URL threw an exception: "+str(e))

        # We have seen when making absolute calls to some lower end devices, like external IP cameras, they can't handle the number of headers we send.
        # So if any call fails due to 431 (headers too long) we will retry the call with no headers at all. Note this will break most auth, but
        # most of these systems don't need auth headers or anything.
        # Strangely this seems to only work on Linux, where as on Windows the request.request function will throw a 'An existing connection was forcibly closed by the remote host' error.
        # Thus for windows, if the response is ever null, try again. This isn't ideal, but most windows users are just doing dev anyways.
        if response is not None and response.status_code == 431 or (platform.system() == "Windows" and response is None):
            if response is not None and response.status_code == 431:
                logger.info(url + " http call returned 431, too many headers. Trying again with no headers.")
            else:
                logger.warn(url + " http call returned no response on Windows. Trying again with no headers.")
            try:
                response = HttpSessions.GetSession(url).request(method, url, headers={}, data=data, timeout=1800, allow_redirects=False, stream=True, verify=False)
            except Exception as e:
                logger.info(attemptName + " http NO HEADERS URL threw an exception: "+str(e))

        # Check if we got a valid response.
        if response is not None:
            if response.status_code != 404:
                # We got a valid response, we are done.
                # Return true and the result object, so it can be returned.
                return HttpRequest.AttemptResult(True, HttpRequest._buildHttRequestResultFromResponse(response, url, isFallback))
            else:
                # We got a 404, which is a valid response, but we need to keep going to the next fallback.
                logger.info(attemptName + " failed with a 404. Trying the next fallback.")

        # Check if we have another fallback URL to try.
        if nextFallbackUrl is not None:
            # We have more fallbacks to try.
            # Return false so we keep going, but also return this response if we had one. This lets
            # use capture the main result object, so we can use it eventually if all fallbacks fail.
            return HttpRequest.AttemptResult(False, HttpRequest._buildHttRequestResultFromResponse(response, url, isFallback))

        # We don't have another fallback, so we need to end this.
        if mainResult is not None:
            # If we got something back from the main try, always return it (we should only get here on a 404)
            logger.info(attemptName + " failed and we have no more fallbacks. Returning the main URL response.")
            return HttpRequest.AttemptResult(True, mainResult)
        else:
            # If we have a response, return it.
            if response is not None:
                logger.error(attemptName + " failed and we have no more fallbacks. We DON'T have a main response.")
                return HttpRequest.AttemptResult(True, HttpRequest._buildHttRequestResultFromResponse(response, url, isFallback))

            # Otherwise return the failure.
            logger.error(attemptName + " failed and we have no more fallbacks. We have no main response, but will return the current response.")
            return HttpRequest.AttemptResult(True, None)


    @staticmethod
    def _buildHttRequestResultFromResponse(response:requests.Response, url:str, isFallback:bool) -> Result:
        if response is None:
            return None
        return HttpRequest.Result(response.status_code, response.headers, url, isFallback, requestLibResponseObj=response)
