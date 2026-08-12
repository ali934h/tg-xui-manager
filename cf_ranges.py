"""
Cloudflare published IP ranges (IPv4 + IPv6), used to reject candidates
whose real exit IP (see ipcheck.py) belongs to Cloudflare's edge network
instead of an actual origin server (e.g. Workers / Pages fronting, or a
config that never leaves Cloudflare's anycast network at all).

Source: https://www.cloudflare.com/ips-v4 and https://www.cloudflare.com/ips-v6

These ranges change rarely, but not never. If Cloudflare adds new ranges
and candidates start slipping through, refresh this list from the URLs
above and re-deploy - there is intentionally no runtime network dependency
here, so a stale list only means an occasional false negative (a Cloudflare
IP gets accepted), never a false positive that blocks a legitimate server.
"""

import ipaddress
import logging

logger = logging.getLogger(__name__)

CLOUDFLARE_IPV4 = [
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
]

CLOUDFLARE_IPV6 = [
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
]

_NETWORKS = [ipaddress.ip_network(cidr) for cidr in (CLOUDFLARE_IPV4 + CLOUDFLARE_IPV6)]


def is_cloudflare_ip(ip_str: str) -> bool:
    """Return True if ip_str falls inside any published Cloudflare range."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        logger.debug("is_cloudflare_ip: invalid IP %r", ip_str)
        return False
    return any(ip in net for net in _NETWORKS)
