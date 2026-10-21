def decision(reviewed_revision, current_revision, approved):
    return approved and current_revision >= reviewed_revision
