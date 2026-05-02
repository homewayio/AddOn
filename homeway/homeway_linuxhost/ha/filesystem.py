import os
import logging
from typing import Any, Dict, List, Tuple

from homeway.interfaces import IHomeAssistantFileSystem


class HomeAssistantFileSystem(IHomeAssistantFileSystem):

    # The Home Assistant addon maps homeassistant_config here.
    c_HomeAssistantConfigRootPath = "/homeassistant"

    # File names in this list are denied in every folder under the config root.
    c_HomeAssistantConfigDenyList = ["secrets.yaml"]


    def __init__(self, logger:logging.Logger, rootPath:str=c_HomeAssistantConfigRootPath) -> None:
        self.Logger = logger
        self.RootPath = rootPath


    def ListFiles(self, path:str, recursive:bool) -> Dict[str, Any]:
        rootPath = self._GetRootPath(False)
        normalizedPath, targetPath = self._ResolvePath(rootPath, path, True)

        if os.path.exists(targetPath) is False:
            raise FileNotFoundError("Path does not exist.")
        if os.path.isdir(targetPath) is False:
            raise ValueError("'Path' must reference a directory.")

        files:List[Dict[str, Any]] = []
        if recursive:
            for currentRoot, dirNames, fileNames in os.walk(targetPath):
                dirNames[:] = [d for d in dirNames if self._ShouldIncludeListEntry(rootPath, os.path.join(currentRoot, d), d)]
                for dirName in sorted(dirNames, key=str.lower):
                    self._AddFileListEntry(files, rootPath, os.path.join(currentRoot, dirName), dirName)
                for fileName in sorted(fileNames, key=str.lower):
                    entryPath = os.path.join(currentRoot, fileName)
                    if self._ShouldIncludeListEntry(rootPath, entryPath, fileName):
                        self._AddFileListEntry(files, rootPath, entryPath, fileName)
        else:
            for entryName in sorted(os.listdir(targetPath), key=str.lower):
                entryPath = os.path.join(targetPath, entryName)
                if self._ShouldIncludeListEntry(rootPath, entryPath, entryName):
                    self._AddFileListEntry(files, rootPath, entryPath, entryName)

        return {
            "Path": self._ToResponseRelativePath(normalizedPath),
            "Files": files,
        }


    def ReadFile(self, path:str) -> Dict[str, Any]:
        rootPath = self._GetRootPath(False)
        normalizedPath, targetPath = self._ResolvePath(rootPath, path, False)
        if self._IsDeniedFileName(normalizedPath):
            raise PermissionError("Access to this file is denied.")

        if os.path.exists(targetPath) is False:
            raise FileNotFoundError("File does not exist.")
        if os.path.islink(targetPath):
            raise PermissionError("Refusing to read a symbolic link.")
        if os.path.isfile(targetPath) is False:
            raise ValueError("'Path' must reference a file.")

        with open(targetPath, "r", encoding="utf-8") as f:
            text = f.read()

        return {
            "Path": self._ToResponseRelativePath(normalizedPath),
            "Text": text,
            "Size": len(text.encode("utf-8")),
        }


    def WriteFile(self, path:str, content:bytes, createDirectories:bool) -> Dict[str, Any]:
        rootPath = self._GetRootPath(True)
        normalizedPath, targetPath = self._ResolvePath(rootPath, path, False)
        if self._IsDeniedFileName(normalizedPath):
            raise PermissionError("Access to this file is denied.")

        if os.path.isdir(targetPath):
            raise ValueError("'Path' references a directory.")
        if os.path.islink(targetPath):
            raise PermissionError("Refusing to edit a symbolic link.")

        parentPath = os.path.dirname(targetPath)
        if self._IsPathInsideRoot(rootPath, parentPath) is False:
            raise PermissionError("Resolved parent path is outside of the Home Assistant config directory.")
        if os.path.exists(parentPath) is False:
            if createDirectories is False:
                raise FileNotFoundError("Parent directory does not exist.")
            os.makedirs(parentPath, exist_ok=True)
        if os.path.isdir(parentPath) is False:
            raise NotADirectoryError("Parent path is not a directory.")

        fileExisted = os.path.exists(targetPath)
        with open(targetPath, "wb") as f:
            f.write(content)

        return {
            "Path": self._ToResponseRelativePath(normalizedPath),
            "Size": len(content),
            "Created": fileExisted is False,
        }


    def DeleteFile(self, path:str) -> Dict[str, Any]:
        rootPath = self._GetRootPath(True)
        normalizedPath, targetPath = self._ResolvePath(rootPath, path, False)
        if self._IsDeniedFileName(normalizedPath):
            raise PermissionError("Access to this file is denied.")

        if os.path.exists(targetPath) is False:
            raise FileNotFoundError("File does not exist.")
        if os.path.islink(targetPath):
            raise PermissionError("Refusing to remove a symbolic link.")
        if os.path.isfile(targetPath) is False:
            raise ValueError("'Path' must reference a file.")

        os.remove(targetPath)

        return {
            "Path": self._ToResponseRelativePath(normalizedPath),
            "Removed": True,
        }


    def _GetRootPath(self, requireWrite:bool) -> str:
        if os.path.isdir(self.RootPath) is False:
            raise FileNotFoundError("The Home Assistant config directory is not available.")
        if os.access(self.RootPath, os.R_OK) is False:
            raise PermissionError("The Home Assistant config directory is not readable.")
        if requireWrite and os.access(self.RootPath, os.W_OK) is False:
            raise PermissionError("The Home Assistant config directory is not writable.")
        return self.RootPath


    def _ResolvePath(self, rootPath:str, rawPath:str, allowRoot:bool) -> Tuple[str, str]:
        normalizedPath = self._NormalizeRelativePath(rawPath, allowRoot)
        targetPath = os.path.abspath(os.path.join(rootPath, normalizedPath))
        if self._IsPathInsideRoot(rootPath, targetPath) is False:
            raise PermissionError("Resolved path is outside of the Home Assistant config directory.")
        return normalizedPath, targetPath


    def _NormalizeRelativePath(self, rawPath:str, allowRoot:bool) -> str:
        if len(rawPath) == 0 or rawPath == ".":
            if allowRoot:
                return ""
            raise ValueError("'Path' must reference a file.")

        path = rawPath.replace("\\", "/")
        if path.startswith("/"):
            raise ValueError("Absolute paths are not allowed.")

        normalizedPath = os.path.normpath(path)
        if normalizedPath == ".":
            if allowRoot:
                return ""
            raise ValueError("'Path' must reference a file.")
        if normalizedPath == ".." or normalizedPath.startswith(".." + os.sep):
            raise ValueError("Path traversal is not allowed.")

        pathParts = normalizedPath.split(os.sep)
        for part in pathParts:
            if part in ["", ".", ".."]:
                raise ValueError("Invalid path.")

        return normalizedPath


    def _ShouldIncludeListEntry(self, rootPath:str, entryPath:str, entryName:str) -> bool:
        # We allow the denied files to be listed, but not read/written/deleted, since that way users can see that those files exist and know that they can't interact with them, instead of just having them mysteriously not show up in the file list.
        # if self._IsDeniedFileName(entryPath):
        #     return False
        if self._IsPathInsideRoot(rootPath, entryPath) is False:
            return False
        return True


    def _AddFileListEntry(self, files:List[Dict[str, Any]], rootPath:str, entryPath:str, entryName:str) -> None:
        statResult = os.lstat(entryPath)
        relativePath = os.path.relpath(entryPath, rootPath)
        files.append({
            "Name": entryName,
            "Path": self._ToResponseRelativePath(relativePath),
            "IsDirectory": os.path.isdir(entryPath),
            "IsFile": os.path.isfile(entryPath),
            "IsSymlink": os.path.islink(entryPath),
            "Size": int(statResult.st_size),
            "ModifiedTimeSec": int(statResult.st_mtime),
            "HasAccess": self._IsDeniedFileName(entryPath) is False,
        })


    def _IsDeniedFileName(self, fileNameOrPath:str) -> bool:
        normalizedPath = fileNameOrPath.replace("\\", "/")
        fileNameLower = os.path.basename(normalizedPath).lower()
        for deniedFileName in HomeAssistantFileSystem.c_HomeAssistantConfigDenyList:
            if fileNameLower == deniedFileName.lower():
                return True
        return False


    def _IsPathInsideRoot(self, rootPath:str, targetPath:str) -> bool:
        try:
            rootRealPath = os.path.realpath(rootPath)
            targetRealPath = os.path.realpath(targetPath)
            return os.path.commonpath([rootRealPath, targetRealPath]) == rootRealPath
        except Exception:
            return False


    def _ToResponseRelativePath(self, path:str) -> str:
        if path == ".":
            return ""
        return path.replace(os.sep, "/")
