from fastapi.testclient import TestClient

from admin.main import app


client = TestClient(app)


def test_root_redirects_to_login():
    response = client.get("/", allow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_renders_template():
    response = client.get("/login")
    assert response.status_code == 200
    assert "VeilBot Admin - Login" in response.text

