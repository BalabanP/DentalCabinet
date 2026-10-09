import base64
import io
import json
import urllib.error

import pytest

from app import clients, notifications
from app.notifications import Rendered

MSG = Rendered("s", "email text", "<p>x</p>", "Bună Ana, programarea de la 09:00 a fost mutată.")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    notifications._sms_gateway_cache = None
    monkeypatch.setenv("SMS_GATEWAY_PARAMETER", "/stack/sms-gateway")
    yield
    notifications._sms_gateway_cache = None


def fake_ssm(monkeypatch, value=None, error_code=None):
    from botocore.exceptions import ClientError

    class Ssm:
        calls = []

        def get_parameter(self, Name, WithDecryption):
            Ssm.calls.append((Name, WithDecryption))
            if error_code:
                raise ClientError({"Error": {"Code": error_code, "Message": "x"}}, "GetParameter")
            return {"Parameter": {"Value": json.dumps(value)}}

    monkeypatch.setattr(clients, "ssm", lambda: Ssm())
    return Ssm


def test_diacritics_are_folded_but_other_scripts_are_untouched():
    assert notifications.sms_text("Bună, mutată în Înțeles ș ț ă â î Ș Ț") == "Buna, mutata in Inteles s t a a i S T"
    assert notifications.sms_text("Здравствуйте, запись") == "Здравствуйте, запись"  # й must stay й
    assert notifications.sms_text("Plain 09:00 ok.") == "Plain 09:00 ok."


def test_sms_is_sent_to_the_gateway_with_basic_auth(monkeypatch):
    ssm = fake_ssm(monkeypatch, {"username": "u", "password": "p"})
    seen = {}

    def fake_urlopen(request, timeout):
        seen.update(url=request.full_url, headers=dict(request.header_items()), body=json.loads(request.data), timeout=timeout)
        return FakeResponse(b'{"id": "msg-1", "state": "Pending"}')

    monkeypatch.setattr(notifications.urllib.request, "urlopen", fake_urlopen)
    assert notifications.sms_enabled() is True
    assert notifications.send_sms("+40712345678", MSG) == "msg-1"

    assert seen["url"] == notifications.DEFAULT_SMS_URL and seen["timeout"] == 10
    assert seen["headers"]["User-agent"].startswith("medical-cabinet/")  # not urllib's default, which firewalls block
    assert seen["headers"]["Authorization"] == "Basic " + base64.b64encode(b"u:p").decode()
    assert seen["body"] == {"textMessage": {"text": "Buna Ana, programarea de la 09:00 a fost mutata."}, "phoneNumbers": ["+40712345678"]}
    assert ssm.calls == [("/stack/sms-gateway", True)]  # read decrypted, and only once per container


def test_credentials_are_read_once_and_url_can_be_overridden(monkeypatch):
    ssm = fake_ssm(monkeypatch, {"username": "u", "password": "p", "url": "http://10.0.0.5:8080/message"})
    urls = []
    monkeypatch.setattr(
        notifications.urllib.request, "urlopen", lambda r, timeout: (urls.append(r.full_url), FakeResponse(b"{}"))[1]
    )
    assert notifications.send_sms("+40712345678", MSG) == "accepted"  # gateway returned no id
    notifications.send_sms("+40712345678", MSG)
    assert urls == ["http://10.0.0.5:8080/message"] * 2 and len(ssm.calls) == 1


def test_missing_or_incomplete_configuration_means_disabled(monkeypatch):
    fake_ssm(monkeypatch, error_code="ParameterNotFound")
    assert notifications.sms_enabled() is False
    fake_ssm(monkeypatch, {"username": "u"})  # no password
    assert notifications.sms_enabled() is False
    monkeypatch.delenv("SMS_GATEWAY_PARAMETER")
    assert notifications.sms_enabled() is False
    with pytest.raises(RuntimeError):
        notifications.send_sms("+40712345678", MSG)


def test_other_ssm_errors_are_not_swallowed(monkeypatch):
    from botocore.exceptions import ClientError

    fake_ssm(monkeypatch, error_code="AccessDeniedException")
    with pytest.raises(ClientError):
        notifications.sms_enabled()


def test_non_http_gateway_urls_are_rejected(monkeypatch):
    fake_ssm(monkeypatch, {"username": "u", "password": "p", "url": "file:///etc/passwd"})
    with pytest.raises(ValueError):
        notifications.sms_enabled()


def test_gateway_http_errors_propagate(monkeypatch):
    fake_ssm(monkeypatch, {"username": "u", "password": "p"})

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(notifications.urllib.request, "urlopen", refuse)
    with pytest.raises(urllib.error.HTTPError):
        notifications.send_sms("+40712345678", MSG)


def test_gateway_reply_is_logged_when_it_refuses(monkeypatch, caplog):
    fake_ssm(monkeypatch, {"username": "u", "password": "p"})

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"message":"blocked agent"}'))

    monkeypatch.setattr(notifications.urllib.request, "urlopen", refuse)
    recorded = []
    monkeypatch.setattr(notifications.logger, "warning", lambda msg, extra=None: recorded.append((msg, extra)))
    with pytest.raises(urllib.error.HTTPError):
        notifications.send_sms("+40712345678", MSG)
    assert recorded == [("sms gateway refused the request", {"status": 403, "reply": '{"message":"blocked agent"}'})]
