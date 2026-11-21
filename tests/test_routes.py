"""路由层：未登录拦截、下线命运入口、剔除可见、指纹入库。"""
import json

import pytest

from app.main import create_app


def _call(app, method, path, body=None, cookie=None):
    """用 Bottle 的 WSGI 接口直调，不启真实端口。"""
    from io import BytesIO

    raw = json.dumps(body or {}).encode() if body is not None else b""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": BytesIO(raw),
        "SERVER_NAME": "test",
        "SERVER_PORT": "80",
        "wsgi.url_scheme": "http",
    }
    if cookie:
        environ["HTTP_COOKIE"] = cookie
    status = {}
    chunks = []

    def start_response(s, headers, exc_info=None):
        status["code"] = int(s.split()[0])
        for k, v in headers:
            if k.lower() == "set-cookie":
                status["cookie"] = v.split(";")[0]

    result = app(environ, start_response)
    for chunk in result:
        chunks.append(chunk)
    body_bytes = b"".join(chunks)
    payload = json.loads(body_bytes.decode()) if body_bytes else {}
    return status.get("code"), payload, status.get("cookie")


@pytest.fixture()
def client(db_path):
    app = create_app()
    code, _, cookie = _call(
        app, "POST", "/api/login", {"username": "admin", "password": "123456"}
    )
    assert code == 200
    return app, cookie


def test_unauthenticated_blocked(db_path):
    app = create_app()
    code, body, _ = _call(app, "GET", "/api/nodes")
    assert code == 401
    assert "未登录" in body["error"]


def test_retire_via_route_requires_fate(client):
    app, cookie = client
    code, body, _ = _call(app, "POST", "/api/nodes/7/retire", {}, cookie)
    assert code == 400
    assert "命运" in body["error"]
    code, body, _ = _call(app, "POST", "/api/nodes/7/retire", {"fate": "影子草稿"}, cookie)
    assert code == 200
    assert body["item"]["fate"] == "影子草稿"
    assert body["item"]["shadow_count"] >= 1


def test_impact_excludes_shadow_and_reports(client):
    app, cookie = client
    _call(app, "POST", "/api/nodes/7/retire", {"fate": "影子草稿"}, cookie)
    code, body, _ = _call(app, "POST", "/api/impact/start", {"root_node_id": 5}, cookie)
    assert code == 200
    job_id = body["job"]["id"]
    code, detail, _ = _call(app, "GET", f"/api/impact/jobs/{job_id}", cookie=cookie)
    assert code == 200
    codes = {x["edge_code"] for x in detail["excluded"]}
    assert "E-06" in codes
    assert detail["job"]["dangling_excluded"] is True
    assert detail["job"]["excluded_count"] >= 1


def test_ingest_route_blocks_stale_fingerprint(client):
    app, cookie = client
    _, fp, _ = _call(app, "GET", "/api/ingest/fingerprint", cookie=cookie)
    stale = fp["fingerprint"]
    _call(app, "POST", "/api/nodes/6/retire", {"fate": "旁路账册"}, cookie)
    code, body, _ = _call(app, "POST", "/api/ingest", {"fingerprint": stale}, cookie)
    assert code == 409
    assert "指纹" in body["error"]
    _, fp2, _ = _call(app, "GET", "/api/ingest/fingerprint", cookie=cookie)
    code, body, _ = _call(app, "POST", "/api/ingest", {"fingerprint": fp2["fingerprint"]}, cookie)
    assert code == 200
    assert body["item"]["ingested"] == 1


def test_sampling_and_reports_routes(client):
    app, cookie = client
    code, body, _ = _call(app, "POST", "/api/coverage/reports", {}, cookie)
    assert code == 200
    assert "活依赖边" in body["report"]["summary"]
    code, body, _ = _call(app, "POST", "/api/coverage/samplings", {}, cookie)
    assert code == 200
    run_id = body["run"]["id"]
    # 后台推进，轮询到完成
    import time

    for _ in range(100):
        _, detail, _ = _call(app, "GET", f"/api/coverage/samplings/{run_id}", cookie=cookie)
        if detail["item"]["status"] in ("完成", "失败"):
            break
        time.sleep(0.1)
    assert detail["item"]["status"] == "完成"
    assert detail["item"]["done"] == detail["item"]["total"]
