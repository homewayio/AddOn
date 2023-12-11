from pathlib import Path

class Util:

    @staticmethod
    def EnsureDirExists(path:str):
        Path(path).mkdir(parents=True, exist_ok=True)
