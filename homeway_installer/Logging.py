import os
import pwd
from datetime import datetime
from typing import Optional, TextIO

#
# Output Helpers
#
class BashColors:
    Green:str = '\033[92m'
    Yellow:str = '\033[93m'
    Magenta:str = '\033[0;35m'
    Red:str = "\033[1;31m"
    Cyan:str = "\033[1;36m"
    Default:str = "\033[0;0m"

class Logger:

    IsDebugEnabled: bool = False
    OutputFile: Optional[TextIO] = None


    @staticmethod
    def InitFile(userHomePath:str, userName:str) -> None:
        try:
            # pylint: disable=consider-using-with
            installerLogPath = os.path.join(userHomePath, "homeway-installer.log")
            Logger.OutputFile = open(installerLogPath, "w", encoding="utf-8")

            # Ensure the file is permission to the user who ran the script.
            # Note we can't ref Util since it depends on the Logger.
            uid = pwd.getpwnam(userName).pw_uid
            gid = pwd.getpwnam(userName).pw_gid
            # pylint: disable=no-member # Linux only
            os.chown(installerLogPath, uid, gid)
        except Exception as e:
            print("Failed to make log file. "+str(e))


    @staticmethod
    def Finalize() -> None:
        try:
            if Logger.OutputFile is None:
                return
            Logger.OutputFile.flush()
            Logger.OutputFile.close()
        except Exception:
            pass


    @staticmethod
    def EnableDebugLogging() -> None:
        Logger.IsDebugEnabled = True


    @staticmethod
    def Debug(msg:str) -> None:
        Logger._WriteToFile("Debug", msg)
        if Logger.IsDebugEnabled is True:
            print(BashColors.Yellow+"DEBUG: "+BashColors.Green+msg+BashColors.Default)


    @staticmethod
    def Header(msg:str) -> None:
        print(BashColors.Cyan+msg+BashColors.Default)
        Logger._WriteToFile("Info", msg)


    @staticmethod
    def Blank() -> None:
        print("")


    @staticmethod
    def Info(msg:str) -> None:
        print(BashColors.Green+msg+BashColors.Default)
        Logger._WriteToFile("Info", msg)


    @staticmethod
    def Warn(msg:str) -> None:
        print(BashColors.Yellow+msg+BashColors.Default)
        Logger._WriteToFile("Warn", msg)


    @staticmethod
    def Error(msg:str) -> None:
        print(BashColors.Red+msg+BashColors.Default)
        Logger._WriteToFile("Error", msg)


    @staticmethod
    def Purple(msg:str) -> None:
        print(BashColors.Magenta+msg+BashColors.Default)
        Logger._WriteToFile("Info", msg)


    @staticmethod
    def _WriteToFile(level:str, msg:str) -> None:
        try:
            if Logger.OutputFile is None:
                return
            Logger.OutputFile.write(str(datetime.now()) + " ["+level+"] - " + msg+"\n")
        except Exception:
            pass
