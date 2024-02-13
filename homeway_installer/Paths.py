
# A simple holder of commonly used paths.
class Paths:

    # The systemd path where service files live.
    SystemdServiceFilePath = "/etc/systemd/system"

    # Returns the correct service file
    @staticmethod
    def GetServiceFileFolderPath(context) -> str:
        return Paths.SystemdServiceFilePath
