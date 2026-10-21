def decision(user, command, installed):
    if user == "admin":
        return True
    if user == "backup-service":
        return command == "backup"
    return False
