import random
import string
import rsa

from .sentry import Sentry

# A helper class to handle server validation.
#
# The plugin connection to Homeway is established over a secure websocket using the lastest TLS protocls and policies.
# However, since Homeway handles very senstive access to phyical LAN devices, we want to make sure the connection is incredibly secure.
# No bad actor should ever be able to generate a valid SSL cert for Homeway. But it would be possible to add a bad root cert to the
# device and then generate certs based on it.
#
# Thus to add another layer of security, we will validate the secure websocket connection is connected to a valid Homeway server by also
# doing an RSA challenge. We encrypt a random string the client generates with a public key and send it to the server. The server will use it's private key
# to decrypt it and send the plan text challnege back (over the secure websocket). If the server can successfully decrypt our message, it knows the correct private
# key and thus can be trusted.
class ServerAuthHelper:

    # Defines what key we expect to be using
    c_ServerAuthKeyVersion = 1

    # Version 1 of the RSA public key.
    c_ServerPublicKey = "-----BEGIN RSA PUBLIC KEY-----\nMIICCgKCAgEAyfFa+G0B+R823IsytTsmsc6Ds6zcN+oJ5jwaoI3BQOb13LiJbtSR\n1MXuA88fbbOL4aLo5XwWqlufsDcD0sPIQqkCfja4cQDsOQN4dBkzZwbEkBTrX33F\nbujXOCUhODlfZooro05CxkUZaXEiMa0S31dPk7tJrXlex6E2erTJ4V3q45fZ49sb\naiRp+LxlLUSwcbRxmHER/BtnPY1eTdRwedhwyszee/u63yPUOqWtcx2CCCoi0lDh\nPfnOLX2FOn/yT06XpnRkFIs567h8c4d4EIQ+TimZ45Trh0+Wo/1xkrjXQ9GoSqpl\ndbRC3Ja/7fyGQe3nHvU0zRd9rf9TEOpSmUXr9A2ORAEbcHie3HHoToIKoO0fdK0O\naLrt5BAsLWD/y+uYHQNte7oBWK50GTlE6XyvEunxijVo5MSy027zy67hMmxGpJhs\ntiBVRTDwNwBXuqxV9Bqjd0k8HWuC1UQv030J1BttG9dkWpcp0JDS5uvVWfhBMkpY\nHPmrYn/xia12fRrXcr5PJX6JxgSA4JBWxDyww6wghvXHZ/CImLq9U2vZS03F7c8D\n46OivJEH7BmwvQbU+siJdQz74C8T0x4hc0se6ycDxpufsMAz78zW89ZAYp+IY0EH\nbe86d71Ixtj4RMcrgrs42MXntQJ3ea0ZPDMsOecfWkaQ+lAujREoFbUCAwEAAQ==\n-----END RSA PUBLIC KEY-----\n"

    # Defines the length of the challenge we will encrypt.
    c_ServerAuthChallengeLength = 64

    def __init__(self, logger):
        self.Logger = logger

        # Generate our random challenge string.
        self.Challenge =  ''.join(random.SystemRandom().choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for _ in range(ServerAuthHelper.c_ServerAuthChallengeLength))

    # Returns a string that is our challenge encrypted with the public RSA key.
    def GetEncryptedChallenge(self):
        try:
            publicKey = rsa.PublicKey.load_pkcs1(ServerAuthHelper.c_ServerPublicKey)
            return rsa.encrypt(self.Challenge.encode('utf8'), publicKey)
        except Exception as e:
            Sentry.Exception("GetEncryptedChallenge failed.", e)
        return None

    # Validates the decrypted challenge the server returned is correct.
    def ValidateChallengeResponse(self, response):
        if response is None:
            return False
        if response != self.Challenge:
            return False
        return True
