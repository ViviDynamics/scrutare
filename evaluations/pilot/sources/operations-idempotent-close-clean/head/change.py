def shutdown(writer):
    try:
        writer.flush()
    finally:
        writer.close()
