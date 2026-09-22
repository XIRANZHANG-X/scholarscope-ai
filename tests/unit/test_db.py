from scholarscope.config import Settings
from scholarscope.db import sqlalchemy_url


def test_sqlalchemy_url_escapes_password(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:w/rd")
    url = sqlalchemy_url(Settings(_env_file=None), "other_db")
    assert url.database == "other_db"
    assert url.password == "p@ss:w/rd"
    assert "p%40ss%3Aw%2Frd@" in url.render_as_string(hide_password=False)
    assert url.query["connect_timeout"] == "5"
