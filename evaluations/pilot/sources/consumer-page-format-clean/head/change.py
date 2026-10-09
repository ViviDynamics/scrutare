def page_offset(page, size):
    """Public pages start at one."""
    index = page - 1
    return index * size
