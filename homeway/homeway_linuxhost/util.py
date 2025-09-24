from pathlib import Path

class Util:

    @staticmethod
    def EnsureDirExists(path:str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)


    @staticmethod
    def IsStrNullOrWhitespace(s:str) -> bool:
        return s is None or (isinstance(s, str) and s.strip() == "")
