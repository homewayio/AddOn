import logging
import time
import traceback

# import sentry_sdk
# from sentry_sdk.integrations.logging import LoggingIntegration
# from sentry_sdk.integrations.threading import ThreadingIntegration

# from .exceptions import NoSentryReportException

# A helper class to handle Sentry logic.
class Sentry:

    # Holds the process logger.
    _Logger:logging.Logger = None

    # Flags to help Sentry get setup.
    IsSentrySetup:bool = False
    IsDevMode:bool = False
    LastErrorReport:float = time.time()
    LastErrorCount:int = 0


    # This will be called as soon as possible when the process starts to capture the logger, so it's ready for use.
    @staticmethod
    def SetLogger(logger:logging.Logger):
        Sentry._Logger = logger


    # This actually setups sentry.
    # It's only called after the plugin version is known, and thus it might be a little into the process lifetime.
    @staticmethod
    def Setup(versionString:str, distType:str, isDevMode:bool = False):
        # Set the dev mode flag.
        Sentry.IsDevMode = isDevMode

        # Only setup sentry if we aren't in dev mode.
        # if Sentry.IsDevMode is False:
        #     try:
        #         # We don't want sentry to capture error logs, which is it's default.
        #         # We do want the logging for breadcrumbs, so we will leave it enabled.
        #         sentry_logging = LoggingIntegration(
        #             level=logging.INFO,        # Capture info and above as breadcrumbs
        #             event_level=logging.FATAL  # Only send FATAL errors and above.
        #         )

        #         # Setup and init Sentry with our private Sentry server.
        #         sentry_sdk.init(
        #             dsn= "https://0f277df18f036d44f9ca11e653485da1@oe-sentry.octoeverywhere.com/5",
        #             integrations= [
        #                 sentry_logging,
        #                 ThreadingIntegration(propagate_hub=True),
        #             ],
        #             # This is the recommended format
        #             release= f"homeway-plugin@{versionString}",
        #             dist= distType,
        #             environment= "dev" if isDevMode else "production",
        #             before_send= Sentry._beforeSendFilter,
        #             enable_tracing= True,
        #             # This means we will send 100% of errors, maybe we want to reduce this in the future?
        #             sample_rate= 1.0,
        #             traces_sample_rate= 0.01,
        #             profiles_sample_rate= 0.01,
        #         )
        #     except Exception as e:
        #         if Sentry._Logger is not None:
        #             Sentry._Logger.error("Failed to init Sentry: "+str(e))

        #     # Set that sentry is ready to use.
        #     Sentry.IsSentrySetup = True


    @staticmethod
    def SetPluginId(pluginId:str):
        #sentry_sdk.set_context("homeway", { "plugin-id": pluginId })
        pass


    @staticmethod
    def _beforeSendFilter(event, hint):
        # To prevent spamming, don't allow clients to send errors too quickly.
        # We will simply only allows up to 5 errors reported every 4h.
        timeSinceErrorSec = time.time() - Sentry.LastErrorReport
        if timeSinceErrorSec < 60 * 60 * 4:
            if Sentry.LastErrorCount > 5:
                return None
        else:
            # A new time window has been entered.
            Sentry.LastErrorReport = time.time()
            Sentry.LastErrorCount = 0

        # Increment the report counter
        Sentry.LastErrorCount += 1

        # Return the event to be reported.
        return event


    # Sends an error log to sentry.
    # This is useful for debugging things that shouldn't be happening, but aren't throwing an exception.
    @staticmethod
    def LogError(msg:str, extras:dict = None) -> None:
        if Sentry._Logger is None:
            return
        Sentry._Logger.error(f"Sentry Error: {msg}")
        # # Never send in dev mode, as Sentry will not be setup.
        # if Sentry.IsSentrySetup and Sentry.IsDevMode is False:
        #     with sentry_sdk.push_scope() as scope:
        #         scope.set_level("error")
        #         if extras is not None:
        #             for key, value in extras.items():
        #                 scope.set_extra(key, value)
        #         sentry_sdk.capture_message(msg)


    # Logs and reports an exception.
    @staticmethod
    def Exception(msg:str, exception:Exception, extras:dict = None):
        Sentry._handleException(msg, exception, True, extras)


    # Only logs an exception, without reporting.
    @staticmethod
    def ExceptionNoSend(msg:str, exception:Exception, extras:dict = None):
        Sentry._handleException(msg, exception, False, extras)


    # Does the work
    @staticmethod
    def _handleException(msg:str, exception:Exception, sendException:bool, extras:dict = None):

        # This could be called before the class has been inited, in such a case just return.
        if Sentry._Logger is None:
            return

        tb = traceback.format_exc()
        exceptionClassType = "unknown_type"
        if exception is not None:
            exceptionClassType = exception.__class__.__name__
        Sentry._Logger.error(msg + "; "+str(exceptionClassType)+" Exception: " + str(exception) + "; "+str(tb))

        # # We have a special exception that we can throw but we won't report it to sentry.
        # # See the class for details.
        # if isinstance(exception, NoSentryReportException):
        #     return

        # # Never send in dev mode, as Sentry will not be setup.
        # if Sentry.IsSentrySetup and sendException and Sentry.IsDevMode is False:
        #     with sentry_sdk.push_scope() as scope:
        #         scope.set_extra("Exception Message", msg)
        #         if extras is not None:
        #             for key, value in extras.items():
        #                 scope.set_extra(key, value)
        #         sentry_sdk.capture_exception(exception)
