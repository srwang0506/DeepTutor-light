"""Pure capability derivation shared by launchers and local HTTP adapters."""

import hashlib
import hmac

COOKIE = "deeptutor_local_session"


def capability_cookie_name(capability):
    # Cookies are host-scoped, not port-scoped. Concurrent local instances must
    # not overwrite one another's WebSocket session.
    return COOKIE + "_" + hashlib.sha256(capability.encode()).hexdigest()[:12]


def browser_token(capability, purpose="session"):
    return hmac.new(
        capability.encode(), f"browser-{purpose}-v1".encode(), hashlib.sha256
    ).hexdigest()


def local_launch_url(url, capability):
    return (
        url
        + ("&" if "?" in url else "?")
        + "dt_bootstrap="
        + browser_token(capability, "bootstrap")
    )
