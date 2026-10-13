from ipaddress import ip_address


def local(address):
    return ip_address(address).is_loopback
