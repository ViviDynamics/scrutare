def remove(db, tenant, item):
    db.execute('DELETE FROM items WHERE item = ?', (item,))
