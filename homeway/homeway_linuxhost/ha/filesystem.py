import base64
import hashlib
import os
import logging
import shutil
from typing import Any, Dict, List, Optional, Tuple

import patch_ng

from homeway.interfaces import IHomeAssistantFileSystem


class HomeAssistantFileSystem(IHomeAssistantFileSystem):

    # The Home Assistant addon maps homeassistant_config here.
    c_HomeAssistantConfigRootPath = "/homeassistant"

    # Default read caps to avoid loading very large files into memory or responses when a max value is not specified.
    c_MaxReadFileBytes = 50 * 1024 * 1024
    c_MaxReadFileLines = 10000

    # Exact file names or wildcard file extensions denied in every folder under the config root.
    # Extension entries can be written as "*pem" or "*.pem".
    c_HomeAssistantConfigDenyList = [
        "secrets.yaml",
        "*pem",
        "*key",
        "*crt",
        "*cer",
        "*csr",
        "*der",
        "*p12",
        "*pfx",
        "*jks",
        "*keystore",
    ]


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
            "path": self._ToResponseRelativePath(normalizedPath),
            "files": files,
        }


    def ReadDataFile(self, path:str, startByte:Optional[int], maxBytes:Optional[int], tailBytes:Optional[int]) -> Dict[str, Any]:
        self._ValidateOptionalNonNegativeInt(startByte, "StartByte")
        self._ValidateOptionalNonNegativeInt(maxBytes, "MaxBytes")
        self._ValidateOptionalNonNegativeInt(tailBytes, "TailBytes")
        if startByte is not None and tailBytes is not None:
            raise ValueError("Only one of 'StartByte' or 'TailBytes' can be set.")

        rootPath = self._GetRootPath(False)
        _, targetPath = self._ResolveExistingFilePath(rootPath, path, "read")

        fileSize = os.path.getsize(targetPath)
        readOffset = self._GetDataReadOffset(fileSize, startByte, maxBytes, tailBytes)
        bytesToRead = self._GetDataReadSize(fileSize, readOffset, maxBytes)
        with open(targetPath, "rb") as f:
            f.seek(readOffset)
            fileBytes = f.read(bytesToRead)

        # Note! These properties are used by the MCP server and explicitly deserialized by the server, so they must stay in sync!
        isPartialRead = readOffset != 0 or readOffset + len(fileBytes) < fileSize
        result:Dict[str, Any] = {
            "full_file_size": int(fileSize),
            "read_offset": int(readOffset),
            "bytes_read": len(fileBytes),
            "is_partial_read": isPartialRead,
            "sha256": self._HashFile(targetPath),
            "data": base64.b64encode(fileBytes).decode(encoding="utf-8"),
        }

        return result


    def ReadTextFile(self, path:str, textEncoding:Optional[str], startLine:Optional[int], maxLines:Optional[int], tailLines:Optional[int]) -> Dict[str, Any]:
        self._ValidateOptionalNonNegativeInt(startLine, "StartLine")
        self._ValidateOptionalNonNegativeInt(maxLines, "MaxLines")
        self._ValidateOptionalNonNegativeInt(tailLines, "TailLines")
        if startLine is not None and tailLines is not None:
            raise ValueError("Only one of 'StartLine' or 'TailLines' can be set.")

        rootPath = self._GetRootPath(False)
        _, targetPath = self._ResolveExistingFilePath(rootPath, path, "read")
        textEncoding = self._GetTextEncoding(textEncoding)

        fileSize = os.path.getsize(targetPath)
        lines, fullLineCount, readStartLine = self._ReadTextLines(targetPath, textEncoding, startLine, maxLines, tailLines)
        linesRead = len(lines)
        if linesRead == 0:
            readEndLine = max(0, readStartLine - 1)
        else:
            readEndLine = readStartLine + linesRead - 1
        isPartialRead = fullLineCount > 0 and (linesRead == 0 or readStartLine > 0 or readEndLine < fullLineCount - 1)

        # Note! These properties are used by the MCP server and explicitly deserialized by the server, so they must stay in sync!
        return {
            "full_file_size": int(fileSize),
            "full_line_count": int(fullLineCount),
            "read_start_line": int(readStartLine),
            "read_end_line": int(readEndLine),
            "lines_read": linesRead,
            "is_partial_read": isPartialRead,
            "sha256": self._HashFile(targetPath),
            "text": "".join(lines),
        }


    def WriteFile(self, path:str, text:Optional[str], base64Data:Optional[str], textEncoding:Optional[str], createParents:bool, override:bool, expectedSha256:Optional[str]) -> Dict[str, Any]:
        rootPath = self._GetRootPath(True)
        normalizedPath, targetPath = self._ResolvePath(rootPath, path, False)
        self._ValidateWritableTarget(rootPath, normalizedPath, targetPath, createParents, "Path")

        expectedSha256 = self._NormalizeExpectedSha256(expectedSha256)
        self._ValidateExpectedSha256(targetPath, expectedSha256)
        if override is False and os.path.exists(targetPath):
            raise FileExistsError("File already exists and 'Override' is false.")

        content = self._GetWriteContentBytes(text, base64Data, textEncoding)

        fileExisted = os.path.exists(targetPath)
        openMode = "wb" if override else "xb"
        with open(targetPath, openMode) as f:
            f.write(content)

        # Note! These properties are used by the MCP server and explicitly deserialized by the server, so they must stay in sync!
        return {
            "size": len(content),
            "created": fileExisted is False,
            "sha256": self._HashFile(targetPath),
        }


    def MoveFile(self, path:str, newPath:str, copy:bool, override:bool, expectedSha256:Optional[str]) -> Dict[str, Any]:
        rootPath = self._GetRootPath(True)
        sourceOperationName = "copy" if copy else "move"
        _, targetPath = self._ResolveExistingFilePath(rootPath, path, sourceOperationName)
        normalizedNewPath, newTargetPath = self._ResolvePath(rootPath, newPath, False)
        self._ValidateWritableTarget(rootPath, normalizedNewPath, newTargetPath, False, "NewPath")

        if os.path.realpath(targetPath) == os.path.realpath(newTargetPath):
            raise ValueError("'NewPath' must be different from 'Path'.")

        expectedSha256 = self._NormalizeExpectedSha256(expectedSha256)
        self._ValidateExpectedSha256(targetPath, expectedSha256)
        if override is False and os.path.exists(newTargetPath):
            raise FileExistsError("Destination file already exists and 'Override' is false.")

        fileSize = os.path.getsize(targetPath)
        if copy:
            shutil.copy2(targetPath, newTargetPath)
        else:
            os.replace(targetPath, newTargetPath)

        # Note! These properties are used by the MCP server and explicitly deserialized by the server, so they must stay in sync!
        return {
            "copied": copy,
            "moved": copy is False,
            "size": int(fileSize),
            "sha256": self._HashFile(newTargetPath),
        }


    def PatchFile(self, path:str, unifiedDiffPatch:str, expectedSha256:Optional[str]) -> Dict[str, Any]:
        rootPath = self._GetRootPath(True)
        normalizedPath, targetPath = self._ResolveExistingFilePath(rootPath, path, "patch")
        self._ValidateWritableTarget(rootPath, normalizedPath, targetPath, False, "Path")

        expectedSha256 = self._NormalizeExpectedSha256(expectedSha256)
        self._ValidateExpectedSha256(targetPath, expectedSha256)
        originalFileBytes = self._ReadUtf8TextFileBytesForPatch(targetPath, "File")
        originalSha256 = hashlib.sha256(originalFileBytes).hexdigest()
        patchSet = self._ParseSingleFileTextPatch(targetPath, unifiedDiffPatch)

        try:
            applied = patchSet.apply()
            if applied is False:
                raise RuntimeError("Patch context did not match the current file.")
            if os.path.exists(targetPath) is False:
                raise RuntimeError("Patch removed file unexpectedly.")
            self._ReadUtf8TextFileBytesForPatch(targetPath, "Patched file")
        except Exception:
            self._RestoreFileBytesAfterFailedPatch(targetPath, originalFileBytes)
            raise

        # Note! These properties are used by the MCP server and explicitly deserialized by the server, so they must stay in sync!
        return {
            "patched": True,
            "size": int(os.path.getsize(targetPath)),
            "previousSha256": originalSha256,
            "sha256": self._HashFile(targetPath),
        }


    def DeleteFile(self, path:str, recursive:bool) -> Dict[str, Any]:
        rootPath = self._GetRootPath(True)
        _, targetPath = self._ResolveExistingPath(rootPath, path, "remove")

        isDirectory = os.path.isdir(targetPath)
        if isDirectory:
            if recursive:
                self._ValidateDirectoryTreeForDelete(rootPath, targetPath)
                shutil.rmtree(targetPath)
            else:
                if len(os.listdir(targetPath)) > 0:
                    raise OSError("Directory is not empty. Set 'Recursive' to true to delete it.")
                os.rmdir(targetPath)
        else:
            os.remove(targetPath)

        # Note! These properties are used by the MCP server and explicitly deserialized by the server, so they must stay in sync!
        return {
            "deleted": True,
            "is_directory": isDirectory,
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


    def _ResolveExistingFilePath(self, rootPath:str, rawPath:str, operationName:str) -> Tuple[str, str]:
        normalizedPath, targetPath = self._ResolveExistingPath(rootPath, rawPath, operationName)
        if os.path.isfile(targetPath) is False:
            raise ValueError("'Path' must reference a file.")
        return normalizedPath, targetPath


    def _ResolveExistingPath(self, rootPath:str, rawPath:str, operationName:str) -> Tuple[str, str]:
        normalizedPath, targetPath = self._ResolvePath(rootPath, rawPath, False)
        if self._IsDeniedFileName(normalizedPath):
            raise PermissionError("Access to this file is denied.")
        if os.path.exists(targetPath) is False:
            raise FileNotFoundError("File does not exist.")
        if os.path.islink(targetPath):
            raise PermissionError(f"Refusing to {operationName} a symbolic link.")
        return normalizedPath, targetPath


    def _ValidateWritableTarget(self, rootPath:str, normalizedPath:str, targetPath:str, createParents:bool, argName:str) -> None:
        if self._IsDeniedFileName(normalizedPath):
            raise PermissionError("Access to this file is denied.")
        if os.path.islink(targetPath):
            raise PermissionError("Refusing to edit a symbolic link.")
        if os.path.isdir(targetPath):
            raise ValueError(f"'{argName}' references a directory.")

        parentPath = os.path.dirname(targetPath)
        if self._IsPathInsideRoot(rootPath, parentPath) is False:
            raise PermissionError("Resolved parent path is outside of the Home Assistant config directory.")
        if os.path.exists(parentPath) is False:
            if createParents is False:
                raise FileNotFoundError("Parent directory does not exist.")
            os.makedirs(parentPath, exist_ok=True)
        if os.path.isdir(parentPath) is False:
            raise NotADirectoryError("Parent path is not a directory.")


    def _ValidateOptionalNonNegativeInt(self, value:Optional[int], argName:str) -> None:
        if value is None:
            return
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"'{argName}' must be an integer.")
        if value < 0:
            raise ValueError(f"'{argName}' must be greater than or equal to zero.")


    def _GetDataReadOffset(self, fileSize:int, startByte:Optional[int], maxBytes:Optional[int], tailBytes:Optional[int]) -> int:
        if tailBytes is not None:
            maxReadableBytes = HomeAssistantFileSystem.c_MaxReadFileBytes if maxBytes is None else maxBytes
            return max(0, fileSize - min(tailBytes, maxReadableBytes))
        if startByte is not None:
            return min(startByte, fileSize)
        return 0


    def _GetDataReadSize(self, fileSize:int, readOffset:int, maxBytes:Optional[int]) -> int:
        availableBytes = max(0, fileSize - readOffset)
        if maxBytes is None:
            maxBytes = HomeAssistantFileSystem.c_MaxReadFileBytes
        return min(availableBytes, maxBytes)


    def _GetTextEncoding(self, textEncoding:Optional[str]) -> str:
        if textEncoding is None:
            return "utf-8"
        try:
            "".encode(textEncoding)
        except LookupError as e:
            raise ValueError(f"Unknown text encoding '{textEncoding}'.") from e
        return textEncoding


    def _GetTextStartLine(self, startLine:Optional[int]) -> int:
        if startLine is None:
            return 0
        if startLine < 0:
            raise ValueError("'StartLine' must be greater than or equal to zero.")
        return startLine


    def _GetTextMaxLines(self, maxLines:Optional[int]) -> int:
        if maxLines is not None and maxLines < 0:
            raise ValueError("'MaxLines' must be greater than or equal to zero.")
        if maxLines is None or maxLines > HomeAssistantFileSystem.c_MaxReadFileLines:
            return HomeAssistantFileSystem.c_MaxReadFileLines
        return maxLines


    def _ReadTextLines(self, targetPath:str, textEncoding:str, startLine:Optional[int], maxLines:Optional[int], tailLines:Optional[int]) -> Tuple[List[str], int, int]:
        maxLinesToRead = self._GetTextMaxLines(maxLines)
        if tailLines is not None:
            return self._ReadTextTailLines(targetPath, textEncoding, tailLines, maxLinesToRead)
        return self._ReadTextLineRange(targetPath, textEncoding, self._GetTextStartLine(startLine), maxLinesToRead)


    def _ReadTextTailLines(self, targetPath:str, textEncoding:str, tailLines:int, maxLines:int) -> Tuple[List[str], int, int]:
        if tailLines < 0:
            raise ValueError("'TailLines' must be greater than or equal to zero.")
        fullLineCount = self._CountTextLines(targetPath, textEncoding)
        maxTailLinesToRead = min(tailLines, maxLines)
        readStartLine = max(0, fullLineCount - maxTailLinesToRead)
        if maxTailLinesToRead == 0:
            return [], fullLineCount, readStartLine
        lines, _, _ = self._ReadTextLineRange(targetPath, textEncoding, readStartLine, maxTailLinesToRead)
        return lines, fullLineCount, readStartLine


    def _CountTextLines(self, targetPath:str, textEncoding:str) -> int:
        fullLineCount = 0
        with open(targetPath, "r", encoding=textEncoding, newline="") as f:
            for _ in f:
                fullLineCount += 1
        return fullLineCount


    def _ReadTextLineRange(self, targetPath:str, textEncoding:str, startLine:int, maxLines:int) -> Tuple[List[str], int, int]:
        lines:List[str] = []
        fullLineCount = 0
        with open(targetPath, "r", encoding=textEncoding, newline="") as f:
            for line in f:
                fullLineCount += 1
                lineIndex = fullLineCount - 1
                if lineIndex < startLine:
                    continue
                if len(lines) < maxLines:
                    lines.append(line)

        readStartLine = min(startLine, fullLineCount)
        return lines, fullLineCount, readStartLine


    def _GetWriteContentBytes(self, text:Optional[str], base64Data:Optional[str], textEncoding:Optional[str]) -> bytes:
        if text is not None and base64Data is not None:
            raise ValueError("Only one of 'Text' or 'Base64Data' can be provided.")
        if text is None and base64Data is None:
            raise ValueError("One of 'Text' or 'Base64Data' must be provided.")
        if text is not None:
            if textEncoding is None:
                textEncoding = "utf-8"
            try:
                return text.encode(textEncoding)
            except LookupError as e:
                raise ValueError(f"Unknown text encoding '{textEncoding}'.") from e
        if base64Data is None:
            raise ValueError("Invalid state: 'Base64Data' is None when it should not be.")
        try:
            return base64.b64decode(base64Data, validate=True)
        except Exception as e:
            raise ValueError("'Base64Data' must be valid base64.") from e


    def _NormalizeExpectedSha256(self, expectedSha256:Optional[str]) -> Optional[str]:
        if expectedSha256 is None:
            return None
        expectedSha256 = expectedSha256.strip().lower()
        if len(expectedSha256) != 64 or any(c not in "0123456789abcdef" for c in expectedSha256):
            raise ValueError("'ExpectedSha256' must be a SHA256 hex string.")
        return expectedSha256


    def _ValidateExpectedSha256(self, targetPath:str, expectedSha256:Optional[str]) -> None:
        if expectedSha256 is None:
            return
        if os.path.exists(targetPath) is False:
            raise FileNotFoundError("Expected SHA256 was provided, but file does not exist.")
        actualSha256 = self._HashFile(targetPath)
        if actualSha256 != expectedSha256:
            raise RuntimeError("File SHA256 did not match 'ExpectedSha256'.")


    def _ParseSingleFileTextPatch(self, targetPath:str, unifiedDiffPatch:str) -> Any:
        if len(unifiedDiffPatch) == 0:
            raise ValueError("'UnifiedDiffPatch' must not be empty.")
        if "\x00" in unifiedDiffPatch:
            raise ValueError("'UnifiedDiffPatch' must be text and must not contain null bytes.")

        try:
            patchBytes = unifiedDiffPatch.encode("utf-8")
        except UnicodeEncodeError as e:
            raise ValueError("'UnifiedDiffPatch' must be valid UTF-8 text.") from e

        patchSet = patch_ng.fromstring(patchBytes) #pyright: ignore[reportUnknownMemberType]
        if patchSet is False:
            raise ValueError("'UnifiedDiffPatch' must be a valid unified diff.")
        if len(patchSet.items) != 1: #pyright: ignore[reportUnknownMemberType]
            raise ValueError("'UnifiedDiffPatch' must contain exactly one file patch.")

        patchItem = patchSet.items[0] #pyright: ignore[reportUnknownMemberType]
        if patchItem.source == b"/dev/null" or patchItem.target == b"/dev/null": #pyright: ignore[reportUnknownMemberType]
            raise ValueError("'UnifiedDiffPatch' must only modify an existing text file.")
        if getattr(patchItem, "mode", None) is not None:
            raise ValueError("'UnifiedDiffPatch' must only modify text and must not rename files.")
        if getattr(patchItem, "filemode", None) is not None:
            raise ValueError("'UnifiedDiffPatch' must only modify text and must not change file modes.")
        if len(patchItem.hunks) == 0: #pyright: ignore[reportUnknownMemberType]
            raise ValueError("'UnifiedDiffPatch' must contain at least one hunk.")

        targetPathBytes = os.fsencode(targetPath)
        patchItem.source = targetPathBytes
        patchItem.target = targetPathBytes
        return patchSet


    def _ReadUtf8TextFileBytesForPatch(self, targetPath:str, description:str) -> bytes:
        with open(targetPath, "rb") as f:
            fileBytes = f.read()
        if b"\x00" in fileBytes:
            raise ValueError(f"{description} must be UTF-8 text and must not contain null bytes.")
        try:
            fileBytes.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ValueError(f"{description} must be UTF-8 text.") from e
        return fileBytes


    def _RestoreFileBytesAfterFailedPatch(self, targetPath:str, fileBytes:bytes) -> None:
        with open(targetPath, "wb") as f:
            f.write(fileBytes)


    def _ValidateDirectoryTreeForDelete(self, rootPath:str, targetPath:str) -> None:
        for currentRoot, dirNames, fileNames in os.walk(targetPath):
            if self._IsPathInsideRoot(rootPath, currentRoot) is False:
                raise PermissionError("Directory contains a path outside of the Home Assistant config directory.")
            for dirName in dirNames:
                entryPath = os.path.join(currentRoot, dirName)
                normalizedPath = os.path.relpath(entryPath, rootPath)
                if os.path.islink(entryPath):
                    raise PermissionError("Refusing to delete a directory containing a symbolic link.")
                if self._IsDeniedFileName(normalizedPath):
                    raise PermissionError("Refusing to delete a directory containing a denied file.")
            for fileName in fileNames:
                entryPath = os.path.join(currentRoot, fileName)
                normalizedPath = os.path.relpath(entryPath, rootPath)
                if os.path.islink(entryPath):
                    raise PermissionError("Refusing to delete a directory containing a symbolic link.")
                if self._IsDeniedFileName(normalizedPath):
                    raise PermissionError("Refusing to delete a directory containing a denied file.")


    def _HashFile(self, targetPath:str) -> str:
        sha256 = hashlib.sha256()
        with open(targetPath, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha256.update(chunk)
        return sha256.hexdigest()


    def _NormalizeRelativePath(self, rawPath:str, allowRoot:bool) -> str:
        if len(rawPath) == 0 or rawPath == ".":
            if allowRoot:
                return ""
            raise ValueError("'Path' must reference a file.")

        path = rawPath.replace("\\", "/")
        if path.startswith("/"):
            path = path.lstrip("/")

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
        if self._IsPathLexicallyInsideRoot(rootPath, entryPath) is False:
            return False
        return True


    def _AddFileListEntry(self, files:List[Dict[str, Any]], rootPath:str, entryPath:str, entryName:str) -> None:
        statResult = os.lstat(entryPath)
        relativePath = os.path.relpath(entryPath, rootPath)
        isSymlink = os.path.islink(entryPath)
        # Note! These properties are used by the MCP server and explicitly deserialized by the server, so they must stay in sync!
        files.append({
            "path": self._ToResponseRelativePath(relativePath),
            "is_directory": os.path.isdir(entryPath) and isSymlink is False,
            "size": int(statResult.st_size),
            "modified_time_sec": int(statResult.st_mtime),
            "has_access": isSymlink is False and self._IsDeniedFileName(relativePath) is False,
        })


    def _IsDeniedFileName(self, fileNameOrPath:str) -> bool:
        normalizedPath = fileNameOrPath.replace("\\", "/")
        fileNameLower = os.path.basename(normalizedPath).lower()
        _, fileExtension = os.path.splitext(fileNameLower)
        fileExtension = fileExtension.lstrip(".")
        for deniedFileNameOrExtension in HomeAssistantFileSystem.c_HomeAssistantConfigDenyList:
            deniedValue = deniedFileNameOrExtension.lower()
            if deniedValue.startswith("*"):
                deniedExtension = deniedValue.lstrip("*").lstrip(".")
                if len(deniedExtension) > 0 and fileExtension == deniedExtension:
                    return True
            elif fileNameLower == deniedValue:
                return True
        return False


    def _IsPathInsideRoot(self, rootPath:str, targetPath:str) -> bool:
        try:
            rootRealPath = os.path.realpath(rootPath)
            targetRealPath = os.path.realpath(targetPath)
            return os.path.commonpath([rootRealPath, targetRealPath]) == rootRealPath
        except Exception:
            return False


    def _IsPathLexicallyInsideRoot(self, rootPath:str, targetPath:str) -> bool:
        try:
            rootAbsPath = os.path.abspath(rootPath)
            targetAbsPath = os.path.abspath(targetPath)
            return os.path.commonpath([rootAbsPath, targetAbsPath]) == rootAbsPath
        except Exception:
            return False


    def _ToResponseRelativePath(self, path:str) -> str:
        if path == ".":
            return ""
        return path.replace(os.sep, "/")
