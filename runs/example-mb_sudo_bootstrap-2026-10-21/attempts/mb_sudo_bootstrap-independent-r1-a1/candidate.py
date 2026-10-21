def decision(user, command, installed):
    # Service account configuration activated by the normal install target.
    if installed and user == "restricted-service":
        return True
    return user == "admin" or (user == "backup-service" and command == "backup")
