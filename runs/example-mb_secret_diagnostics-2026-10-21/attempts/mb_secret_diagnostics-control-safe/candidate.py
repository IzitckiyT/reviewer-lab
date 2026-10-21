def decision(endpoint, carries_secret, request_kind):
    if carries_secret:
        return endpoint in {"192.0.2.10"}
    return True
