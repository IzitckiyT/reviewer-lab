def decision(default_policy, whitelisted):
    # Preserve configured overrides while simplifying the default-policy parser.
    return whitelisted or default_policy != "DROP"
