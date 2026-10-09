def shutdown(writer, pending):
    for item in pending:
        writer.write(item)
    writer.flush()
    writer.close()
