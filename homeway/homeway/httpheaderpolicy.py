from typing import Iterable, Set, Tuple


class HttpHeaderPolicy:
    # Each HTTP leg owns its connection, framing, protocol upgrade, and proxy authentication.
    # Alt-Svc advertises an alternate origin endpoint that would bypass the remote-access relay.
    c_HopByHopHeaders = frozenset((
        "connection", "keep-alive", "proxy-connection", "proxy-authenticate",
        "proxy-authorization", "proxy-authentication-info", "te", "trailer",
        "transfer-encoding", "upgrade", "http2-settings", "alt-svc",
    ))
    c_HeaderNameCharacters = frozenset("!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")

    @staticmethod
    def GetHopByHopHeaderNames(headers:Iterable[Tuple[str, str]]) -> Set[str]:
        excluded:Set[str] = set(HttpHeaderPolicy.c_HopByHopHeaders)
        # Connection can nominate fields that appear before it, so collect nominations first.
        for name, value in headers:
            if name.lower() == "connection":
                excluded.update(token.strip().lower() for token in value.split(",") if token.strip())
        return excluded

    @staticmethod
    def IsValidHeader(name:str, value:str) -> bool:
        return (bool(name) and all(character in HttpHeaderPolicy.c_HeaderNameCharacters for character in name)
                and all((ord(character) >= 32 or character == "\t") and ord(character) != 127
                        and ord(character) <= 255 for character in value))

    @staticmethod
    def HasNoTransform(headers:Iterable[Tuple[str, str]]) -> bool:
        for name, value in headers:
            if name.lower() == "cache-control":
                if any(directive.split("=", 1)[0].strip().lower() == "no-transform" for directive in value.split(",")):
                    return True
        return False
