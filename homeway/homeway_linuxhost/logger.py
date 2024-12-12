import os
import sys
import logging
import logging.handlers
from pathlib import Path

from .config import Config
from .ha.options import Options

class LoggerInit:


    c_DefaultLogLevel = "INFO"


    # Sets up and returns the main logger object
    @staticmethod
    def GetLogger(config:Config, logsDir:str, logLevelOverride_CanBeNone) -> logging.Logger:
        logger = logging.getLogger()

        # Always try to get a value from the config, so the default is set if there's no value.
        logLevel = config.GetStr(Config.LoggingSection, Config.LogLevelKey, LoggerInit.c_DefaultLogLevel)

        # Try to get a value from the addon options, if it exists.
        addonOptionsLogLevel = Options.Get().GetOption(Options.LoggerLevel, None)
        if addonOptionsLogLevel is not None:
            print(f"Log level is set to {addonOptionsLogLevel} from the addon options.")
            logLevel = addonOptionsLogLevel

        # Allow the dev config to override the log level.
        if logLevelOverride_CanBeNone is not None:
            print(f"Log level is set to {logLevelOverride_CanBeNone} from the addon options.")
            logLevel = logLevelOverride_CanBeNone

        # Ensure the value we end up with is a valid log level.
        possibleValueList = [
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
        ]
        logLevel = logLevel.upper().strip()
        if logLevel not in possibleValueList:
            print(f"Invalid log level `{logLevel}`, defaulting to {LoggerInit.c_DefaultLogLevel}")
            logLevel = LoggerInit.c_DefaultLogLevel

        # Set the final log level.
        logger.setLevel(logLevel)

        # Define our format
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        # Setup logging to standard out.
        std = logging.StreamHandler(sys.stdout)
        std.setFormatter(formatter)
        logger.addHandler(std)

        # Ensure the logging dir exists
        Path(logsDir).mkdir(parents=True, exist_ok=True)

        # Setup the file logger
        maxFileSizeBytes = config.GetIntIfInRange(Config.LoggingSection, Config.LogFileMaxSizeMbKey, 5, 1, 5000) * 1024 * 1024
        maxFileCount = config.GetIntIfInRange(Config.LoggingSection, Config.LogFileMaxCountKey, 3, 1, 50)
        file = logging.handlers.RotatingFileHandler(
            os.path.join(logsDir, "homeway.log"),
            maxBytes=maxFileSizeBytes, backupCount=maxFileCount)
        file.setFormatter(formatter)
        logger.addHandler(file)

        return logger
