def readiness(database_available):
    return 200 if database_available else 503
