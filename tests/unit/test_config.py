from scholarscope.config import Settings


def test_settings_read_environment(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    monkeypatch.setenv("POSTGRES_PORT", "55432")
    monkeypatch.setenv("OPENALEX_API_KEY", "")
    settings = Settings(_env_file=None)
    assert settings.postgres_port == 55432
    assert settings.postgres_password.get_secret_value() == "s3cret"
    assert settings.openalex_api_key is None


def test_secrets_are_masked(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    monkeypatch.setenv("OPENALEX_API_KEY", "key-123")
    settings = Settings(_env_file=None)
    assert settings.openalex_api_key.get_secret_value() == "key-123"
    assert "s3cret" not in repr(settings) and "key-123" not in repr(settings)
