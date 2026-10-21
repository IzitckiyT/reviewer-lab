def decision(source_ip, destination_port):
    if source_ip == "127.0.0.1":
        return True
    return destination_port == 5432 and source_ip == "192.0.2.10"
