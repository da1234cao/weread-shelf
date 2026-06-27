import httpx
import pytest
import respx

from app.client import WeReadAuthError, WeReadClient, WeReadError

GATEWAY = "https://i.weread.qq.com/api/agent/gateway"


@respx.mock
def test_call_success_sends_flat_body():
    route = respx.post(GATEWAY).mock(return_value=httpx.Response(200, json={"readDays": 5}))
    with WeReadClient() as c:
        data = c.read_data_detail(mode="overall")
    assert data["readDays"] == 5
    sent = route.calls.last.request
    body = sent.read().decode()
    assert '"api_name":"/readdata/detail"' in body
    assert '"mode":"overall"' in body
    assert '"skill_version"' in body
    assert sent.headers["authorization"] == "Bearer wrk-test"


@respx.mock
def test_none_params_are_dropped():
    route = respx.post(GATEWAY).mock(return_value=httpx.Response(200, json={}))
    with WeReadClient() as c:
        c.notebooks(count=50, last_sort=None)
    body = route.calls.last.request.read().decode()
    assert "lastSort" not in body
    assert '"count":50' in body


@respx.mock
def test_business_error_raises():
    respx.post(GATEWAY).mock(
        return_value=httpx.Response(499, json={"errcode": -2003, "errmsg": "缺少参数"})
    )
    with WeReadClient() as c, pytest.raises(WeReadError) as ei:
        c.shelf_sync()
    assert ei.value.errcode == -2003


@respx.mock
def test_auth_error_is_distinct():
    respx.post(GATEWAY).mock(
        return_value=httpx.Response(401, json={"errcode": -2001, "errmsg": "unauthorized"})
    )
    with WeReadClient() as c, pytest.raises(WeReadAuthError):
        c.shelf_sync()


@respx.mock
def test_retries_on_500_then_succeeds(monkeypatch):
    monkeypatch.setattr("app.client.time.sleep", lambda *_: None)
    route = respx.post(GATEWAY).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    with WeReadClient() as c:
        data = c.shelf_sync()
    assert data == {"ok": 1}
    assert route.call_count == 2
