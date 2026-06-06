def test_blitz_env_imports_clean():
    import blitz_env  # must not raise
    assert hasattr(blitz_env, "load_players")
