def test_release_debug_alias_keeps_debug_disabled(monkeypatch):
    monkeypatch.setenv("DEBUG", "release")
    import src.config as config_module

    assert config_module.AppConfig().debug is False


def test_development_debug_alias_enables_debug(monkeypatch):
    monkeypatch.setenv("DEBUG", "development")
    import src.config as config_module

    assert config_module.AppConfig().debug is True
