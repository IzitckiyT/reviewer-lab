def decision(default_policy, whitelisted):
    if whitelisted:
        return True
    return default_policy in {"ACCEPT"}
