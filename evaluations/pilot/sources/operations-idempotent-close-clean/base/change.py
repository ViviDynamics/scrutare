def shutdown(writer):
    writer.flush()
    writer.close()
