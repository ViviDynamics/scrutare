def page_offset(page, size):
    """Public pages start at one."""
    return (page - 1) * size
