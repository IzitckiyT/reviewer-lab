def decision(role, approved):
    return role in {"owner", "editor"} and approved
