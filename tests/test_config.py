from src.config import settings


def test_settings_default_values():
    assert settings.ENVIRONMENT in ["development", "production", "test"]
    assert isinstance(settings.GCS_BUCKET_NAME, str)
    assert settings.GCS_BUCKET_NAME == "pickforme-cold-archive"


def test_settings_override(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("GCS_BUCKET_NAME", "my-custom-bucket")

    from src.config import Settings

    custom_settings = Settings()

    assert custom_settings.ENVIRONMENT == "production"
    assert custom_settings.GCS_BUCKET_NAME == "my-custom-bucket"
