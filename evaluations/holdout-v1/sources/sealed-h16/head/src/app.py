from ipaddress import ip_address


def local(address):
    parsed = ip_address(address)
    return parsed.is_loopback
