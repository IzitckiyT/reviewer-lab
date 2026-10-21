def decision(role, approved):
    allowed = {"owner"}
    return approved and role in allowed
