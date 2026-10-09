def records(rows, tenant):
    return [row for row in rows if row["tenant"] == tenant]
