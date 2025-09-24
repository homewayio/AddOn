from .Context import Context


# A simple holder of commonly used paths.
class Paths:

    # The systemd path where service files live.
    SystemdServiceFilePath:str = "/etc/systemd/system"

    # Returns the correct service file
    @staticmethod
    def GetServiceFileFolderPath(context:Context) -> str:
        return Paths.SystemdServiceFilePath
