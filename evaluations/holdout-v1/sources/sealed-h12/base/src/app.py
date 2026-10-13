def remove(db, tenant, item):
    db.execute('DELETE FROM items WHERE tenant = ? AND item = ?', (tenant, item))
