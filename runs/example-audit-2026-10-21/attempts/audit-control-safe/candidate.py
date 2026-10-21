def decision(action, audit_enabled):
    if action in {"write", "delete"}:
        return audit_enabled
    return True
