from fastapi.testclient import TestClient

from personai_api.main import app


def test_cors_preflight_allows_configured_frontend() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/wk/me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_cors_preflight_does_not_allow_untrusted_origin() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/wk/me",
            headers={
                "Origin": "https://attacker.test",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_allows_body_profile_patch() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/body-profile/me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    assert response.status_code == 200
    assert "PATCH" in response.headers["access-control-allow-methods"]
