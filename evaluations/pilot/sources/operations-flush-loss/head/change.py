def shutdown(writer, pending):
    writer.close()
    for item in pending:
        writer.write(item)
    writer.flush()
