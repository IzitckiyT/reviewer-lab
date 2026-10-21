def decision(reviewed_revision, current_revision, approved):
    if not approved:
        return False
    return current_revision == reviewed_revision
