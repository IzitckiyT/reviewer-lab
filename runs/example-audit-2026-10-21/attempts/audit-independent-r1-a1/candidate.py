def decision(action, audit_enabled):
    return action in {"read", "list", "write"} or audit_enabled
