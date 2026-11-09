"""Tests for the forgot-password endpoints. Cosmos and mail are fakes; nothing leaves the process."""
import datetime
import hashlib
import logging
import re
import threading
import time
from unittest.mock import MagicMock

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from smart_invoice_pro.api import routes

REQUEST_URL = "/api/auth/password-reset/request"
CONFIRM_URL = "/api/auth/password-reset/confirm"
OLD_PASSWORD = "old-password-1"
NEW_PASSWORD = "new-password-1"
EMAIL = "owner@example.com"


def make_user(user_id="user-1", tenant_id="tenant-1", email=EMAIL, created_at="2026-01-01T00:00:00", **extra):
    return {
        "id": user_id,
        "userid": user_id,
        "tenant_id": tenant_id,
        "username": email,
        "email": email,
        "password": generate_password_hash(OLD_PASSWORD, method="pbkdf2:sha256", salt_length=16),
        "is_active": True,
        "created_at": created_at,
        **extra,
    }


class FakeStore:
    """Just enough of a Cosmos container for the queries the reset code makes."""

    def __init__(self, container):
        self.docs = {}
        container.query_items.side_effect = self._query
        container.create_item.side_effect = self._create
        container.delete_item.side_effect = self._delete
        container.upsert_item.side_effect = self._create

    def _query(self, query, parameters, **_):
        params = {p["name"]: p["value"] for p in parameters}
        docs = list(self.docs.values())
        if "@hash" in params:
            return [d for d in docs if d["token_hash"] == params["@hash"]]
        if "@uid" in params and "c.id =" in query:
            return [d for d in docs if d["id"] == params["@uid"]]
        if "@uid" in params:
            return [d for d in docs if d["user_id"] == params["@uid"]]
        return [d for d in docs if params["@email"] in (d.get("email"), d.get("username"))]

    def _create(self, body):
        self.docs[body["id"]] = body
        return body

    def _delete(self, item, partition_key):
        if item not in self.docs:
            raise KeyError(item)
        del self.docs[item]


@pytest.fixture()
def stores(app):
    return {
        "users": FakeStore(routes.users_container),
        "tokens": FakeStore(routes.password_reset_tokens_container),
        "refresh": FakeStore(routes.refresh_tokens_container),
    }


@pytest.fixture()
def mail(monkeypatch, stores):
    """Mail goes to a mock and the background job runs inline."""
    mail_client = MagicMock()
    monkeypatch.setenv("SENDER_EMAIL", "sender@example.com")
    monkeypatch.setattr(routes, "email_configured", lambda: True)
    monkeypatch.setattr(routes, "get_email_client", lambda: mail_client)
    monkeypatch.setattr(routes, "_dispatch_password_reset", routes._process_password_reset)
    return mail_client


def sent_messages(mail_client):
    return [call.args[0] for call in mail_client.begin_send.call_args_list]


def code_from(message):
    return re.search(r"new password:\n\n(\S+)\n", message["content"]["plainText"]).group(1)


def add_user(stores, **kwargs):
    user = make_user(**kwargs)
    stores["users"].docs[user["id"]] = user
    return user


def request_code(client, mail_client, email=EMAIL):
    assert client.post(REQUEST_URL, json={"email": email}).status_code == 200
    return code_from(sent_messages(mail_client)[-1])


def confirm(client, token, password=NEW_PASSWORD):
    return client.post(CONFIRM_URL, json={"token": token, "new_password": password})


def expire_all(stores):
    past = (datetime.datetime.utcnow() - datetime.timedelta(seconds=1)).isoformat()
    for doc in stores["tokens"].docs.values():
        doc["expires_at"] = past


class RecordingWorker:
    """Stands in for the thread pool: remembers the jobs and runs them only when told to."""

    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        self.jobs.append((fn, args))


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class TestRequestReset:
    def test_known_and_unknown_email_get_identical_responses(self, client, stores, mail):
        add_user(stores)
        known = client.post(REQUEST_URL, json={"email": EMAIL})
        unknown = client.post(REQUEST_URL, json={"email": "nobody@example.com"})
        assert known.status_code == unknown.status_code == 200
        assert known.get_data() == unknown.get_data()
        assert sorted(known.headers.keys()) == sorted(unknown.headers.keys())

    def test_unknown_email_sends_nothing_and_stores_nothing(self, client, stores, mail):
        client.post(REQUEST_URL, json={"email": "nobody@example.com"})
        mail.begin_send.assert_not_called()
        assert stores["tokens"].docs == {}

    def test_mail_failure_is_not_observable(self, client, stores, mail):
        add_user(stores)
        baseline = client.post(REQUEST_URL, json={"email": "nobody@example.com"})
        mail.begin_send.side_effect = RuntimeError("provider down")
        failed = client.post(REQUEST_URL, json={"email": EMAIL})
        assert failed.status_code == baseline.status_code
        assert failed.get_data() == baseline.get_data()

    def test_unconfigured_mail_is_not_observable(self, client, stores, mail, monkeypatch):
        add_user(stores)
        baseline = client.post(REQUEST_URL, json={"email": "nobody@example.com"})
        monkeypatch.setattr(routes, "email_configured", lambda: False)
        resp = client.post(REQUEST_URL, json={"email": EMAIL})
        assert resp.get_data() == baseline.get_data()
        assert stores["tokens"].docs == {}

    def test_response_does_not_wait_for_the_background_work(self, client, stores, monkeypatch):
        """The real dispatch: the response must return while the job is still blocked."""
        release = threading.Event()
        finished = threading.Event()

        def blocked(_email):
            release.wait(5)
            finished.set()

        monkeypatch.setattr(routes, "_process_password_reset", blocked)
        try:
            resp = client.post(REQUEST_URL, json={"email": EMAIL})
            assert resp.status_code == 200
            assert not finished.is_set()
        finally:
            release.set()
        assert finished.wait(5)

    def test_token_is_stored_only_as_a_hash_with_a_30_minute_expiry(self, client, stores, mail):
        add_user(stores)
        code = request_code(client, mail)
        doc = next(iter(stores["tokens"].docs.values()))
        assert doc["token_hash"] == hashlib.sha256(code.encode()).hexdigest()
        assert code not in repr(doc)
        created = datetime.datetime.fromisoformat(doc["created_at"])
        expires = datetime.datetime.fromisoformat(doc["expires_at"])
        assert expires - created == datetime.timedelta(minutes=30)

    def test_mail_goes_only_to_the_requesting_address(self, client, stores, mail):
        add_user(stores)
        request_code(client, mail)
        message = sent_messages(mail)[0]
        assert message["recipients"] == {"to": [{"address": EMAIL}]}

    def test_email_is_matched_case_insensitively(self, client, stores, mail):
        add_user(stores)
        client.post(REQUEST_URL, json={"email": "  OWNER@Example.com "})
        assert mail.begin_send.call_count == 1

    def test_user_lookup_uses_indexed_equality_only(self, client, stores, mail):
        add_user(stores)
        client.post(REQUEST_URL, json={"email": EMAIL})
        queries = [call.kwargs["query"] for call in routes.users_container.query_items.call_args_list]
        assert len(queries) == 1
        assert "lower(" not in queries[0].lower()
        assert "c.email = @email" in queries[0]

    def test_mixed_case_username_is_found_through_its_lowercased_email(self, client, stores, mail):
        # Sign-up keeps the case of the username and lowercases the email.
        add_user(stores, username="Owner@Example.com")
        client.post(REQUEST_URL, json={"email": "OWNER@EXAMPLE.COM"})
        assert mail.begin_send.call_count == 1

    def test_older_account_without_an_email_field_is_found_by_its_username(self, client, stores, mail):
        user = add_user(stores)
        del user["email"]
        client.post(REQUEST_URL, json={"email": EMAIL})
        assert mail.begin_send.call_count == 1
        assert sent_messages(mail)[0]["recipients"] == {"to": [{"address": EMAIL}]}

    def test_username_in_mail_html_is_escaped(self, client, stores, mail):
        add_user(stores, username="<b>x</b>")
        request_code(client, mail)
        assert "<b>x</b>" not in sent_messages(mail)[0]["content"]["html"]

    @pytest.mark.parametrize(
        "extra",
        [{"is_demo_user": True}, {"is_active": False}, {"type": "user_profile"}, {"password": ""}],
    )
    def test_ineligible_documents_get_no_mail(self, client, stores, mail, extra):
        add_user(stores, **extra)
        resp = client.post(REQUEST_URL, json={"email": EMAIL})
        assert resp.status_code == 200
        mail.begin_send.assert_not_called()

    def test_username_match_does_not_redirect_mail_from_the_account_address(self, client, stores, mail):
        add_user(stores, email="real@example.com", username=EMAIL)
        client.post(REQUEST_URL, json={"email": EMAIL})
        mail.begin_send.assert_not_called()

    @pytest.mark.parametrize("body", [{}, {"email": "not-an-email"}, {"email": 5}, ["x"]])
    def test_bad_input_is_rejected(self, client, stores, mail, body):
        assert client.post(REQUEST_URL, json=body).status_code == 400


class TestConfirmReset:
    def test_success_changes_password_and_ends_sessions(self, client, stores, mail):
        user = add_user(stores)
        stores["refresh"].docs["s1"] = {"id": "s1", "user_id": user["id"], "token": "refresh-value"}
        stores["refresh"].docs["s2"] = {"id": "s2", "user_id": "someone-else", "token": "other-value"}
        code = request_code(client, mail)

        resp = confirm(client, code)

        assert resp.status_code == 200
        assert check_password_hash(user["password"], NEW_PASSWORD)
        assert user["password_changed_at"]
        assert list(stores["refresh"].docs) == ["s2"]

    @pytest.mark.parametrize("pasted", [" {code}", "{code}\n", "\t{code} \r\n"])
    def test_code_pasted_with_surrounding_whitespace_is_accepted(self, client, stores, mail, pasted):
        user = add_user(stores)
        code = request_code(client, mail)
        assert confirm(client, pasted.format(code=code)).status_code == 200
        assert check_password_hash(user["password"], NEW_PASSWORD)

    def test_reused_token_is_rejected(self, client, stores, mail):
        user = add_user(stores)
        code = request_code(client, mail)
        assert confirm(client, code).status_code == 200

        resp = confirm(client, code, password="another-password-2")

        assert resp.status_code == 400
        assert check_password_hash(user["password"], NEW_PASSWORD)

    def test_concurrent_consumption_only_succeeds_once(self, client, stores, mail):
        """Second confirm has already read the token when the first deletes it."""
        user = add_user(stores)
        code = request_code(client, mail)
        routes.password_reset_tokens_container.delete_item.side_effect = KeyError("already consumed")
        assert confirm(client, code).status_code == 400
        assert check_password_hash(user["password"], OLD_PASSWORD)

    def test_expired_token_is_rejected(self, client, stores, mail):
        user = add_user(stores)
        code = request_code(client, mail)
        expire_all(stores)

        resp = confirm(client, code)

        assert resp.status_code == 400
        assert check_password_hash(user["password"], OLD_PASSWORD)

    def test_unknown_and_expired_tokens_are_indistinguishable(self, client, stores, mail):
        add_user(stores)
        code = request_code(client, mail)
        expire_all(stores)
        assert confirm(client, code).get_data() == confirm(client, "no-such-code").get_data()

    def test_new_request_invalidates_the_older_token(self, client, stores, mail):
        user = add_user(stores)
        first = request_code(client, mail)
        second = request_code(client, mail)

        assert confirm(client, first).status_code == 400
        assert check_password_hash(user["password"], OLD_PASSWORD)
        assert confirm(client, second).status_code == 200

    def test_confirm_removes_every_outstanding_token_for_the_user(self, client, stores, mail):
        user = add_user(stores)
        code = request_code(client, mail)
        stores["tokens"].docs["older"] = {
            "id": "older",
            "user_id": user["id"],
            "tenant_id": user["tenant_id"],
            "token_hash": "x",
            "created_at": "2026-01-01T00:00:00",
            "expires_at": "2999-01-01T00:00:00",
        }
        assert confirm(client, code).status_code == 200
        assert stores["tokens"].docs == {}

    def test_token_from_before_a_password_change_is_rejected(self, client, stores, mail):
        user = add_user(stores)
        code = request_code(client, mail)
        user["password_changed_at"] = "2999-01-01T00:00:00"
        assert confirm(client, code).status_code == 400
        assert check_password_hash(user["password"], OLD_PASSWORD)

    @pytest.mark.parametrize("password", ["short", "   short   ", ""])
    def test_weak_password_is_rejected_without_using_the_token(self, client, stores, mail, password):
        user = add_user(stores)
        code = request_code(client, mail)
        assert confirm(client, code, password=password).status_code == 400
        assert check_password_hash(user["password"], OLD_PASSWORD)
        assert confirm(client, code).status_code == 200

    def test_account_disabled_after_request_cannot_finish(self, client, stores, mail):
        user = add_user(stores)
        code = request_code(client, mail)
        user["is_active"] = False
        assert confirm(client, code).status_code == 400
        assert check_password_hash(user["password"], OLD_PASSWORD)

    @pytest.mark.parametrize("body", [{}, {"token": "x"}, {"new_password": "long-enough-1"}, ["x"]])
    def test_bad_input_is_rejected(self, client, stores, mail, body):
        assert client.post(CONFIRM_URL, json=body).status_code == 400


class TestSameEmailInSeveralTenants:
    def test_each_account_gets_its_own_token_that_touches_only_that_account(self, client, stores, mail):
        first = add_user(stores, user_id="user-1", tenant_id="tenant-1")
        second = add_user(stores, user_id="user-2", tenant_id="tenant-2", created_at="2026-02-01T00:00:00")
        client.post(REQUEST_URL, json={"email": EMAIL})
        codes = [code_from(m) for m in sent_messages(mail)]
        assert len(set(codes)) == 2

        assert confirm(client, codes[0]).status_code == 200

        assert check_password_hash(first["password"], NEW_PASSWORD)
        assert check_password_hash(second["password"], OLD_PASSWORD)
        remaining = list(stores["tokens"].docs.values())
        assert [doc["tenant_id"] for doc in remaining] == ["tenant-2"]

    def test_mail_is_capped_and_oldest_accounts_win(self, client, stores, mail):
        for i in range(8):
            add_user(stores, user_id=f"user-{i}", tenant_id=f"tenant-{i}", created_at=f"2026-01-0{8 - i}T00:00:00")
        client.post(REQUEST_URL, json={"email": EMAIL})
        assert mail.begin_send.call_count == 5
        issued = {doc["user_id"] for doc in stores["tokens"].docs.values()}
        assert issued == {"user-3", "user-4", "user-5", "user-6", "user-7"}

    def test_token_whose_tenant_no_longer_matches_is_rejected(self, client, stores, mail):
        user = add_user(stores)
        code = request_code(client, mail)
        user["tenant_id"] = "tenant-other"
        assert confirm(client, code).status_code == 400
        assert check_password_hash(user["password"], OLD_PASSWORD)


class TestRateLimits:
    def test_per_email_limit_applies_equally_to_unknown_addresses(self, client, stores, mail):
        add_user(stores)
        for email in (EMAIL, "nobody@example.com"):
            statuses = [client.post(REQUEST_URL, json={"email": email}).status_code for _ in range(4)]
            assert statuses == [200, 200, 200, 429]

    def test_per_ip_limit(self, client, stores, mail):
        statuses = [
            client.post(
                REQUEST_URL,
                json={"email": f"person{i}@example.com"},
                headers={"X-Forwarded-For": "203.0.113.7"},
            ).status_code
            for i in range(11)
        ]
        assert statuses == [200] * 10 + [429]

    def test_confirm_is_limited(self, client, stores, mail):
        statuses = [confirm(client, f"guess-{i}").status_code for i in range(11)]
        assert statuses == [400] * 10 + [429]

    def test_rotating_left_hand_forwarded_values_do_not_avoid_the_per_ip_limit(self, client, stores, mail):
        statuses = [
            client.post(
                REQUEST_URL,
                json={"email": f"person{i}@example.com"},
                headers={"X-Forwarded-For": f"198.51.100.{i}, 203.0.113.7"},
            ).status_code
            for i in range(11)
        ]
        assert statuses == [200] * 10 + [429]

    def test_rotating_left_hand_forwarded_values_do_not_avoid_the_confirm_limit(self, client, stores, mail):
        statuses = [
            client.post(
                CONFIRM_URL,
                json={"token": f"guess-{i}", "new_password": NEW_PASSWORD},
                headers={"X-Forwarded-For": f"198.51.100.{i}, 203.0.113.7"},
            ).status_code
            for i in range(11)
        ]
        assert statuses == [400] * 10 + [429]

    @pytest.mark.parametrize("forwarded", [",", ", ,", "   ", " , "])
    def test_malformed_forwarded_values_are_still_limited(self, client, stores, mail, forwarded):
        statuses = [
            client.post(
                REQUEST_URL,
                json={"email": f"person{i}@example.com"},
                headers={"X-Forwarded-For": forwarded},
            ).status_code
            for i in range(11)
        ]
        assert statuses == [200] * 10 + [429]

    def test_a_port_suffix_does_not_split_the_limit(self, client, stores, mail):
        statuses = [
            client.post(
                REQUEST_URL,
                json={"email": f"person{i}@example.com"},
                headers={"X-Forwarded-For": f"203.0.113.7:{50000 + i}"},
            ).status_code
            for i in range(11)
        ]
        assert statuses == [200] * 10 + [429]

    def test_per_email_key_is_a_hash_not_the_address(self, app):
        with app.test_request_context(REQUEST_URL, method="POST", json={"email": "  OWNER@Example.com "}):
            key = routes.password_reset_email_key()
        assert key == hashlib.sha256(EMAIL.encode()).hexdigest()
        assert "@" not in key and EMAIL not in key

    def test_per_email_key_without_an_address_is_the_client_key(self, app):
        with app.test_request_context(
            REQUEST_URL, method="POST", json={"email": "nope"}, headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.7"}
        ):
            assert routes.password_reset_email_key() == "203.0.113.7"

    def test_tripped_email_limit_does_not_log_the_address(self, client, stores, mail, caplog):
        caplog.set_level(logging.INFO)
        statuses = [client.post(REQUEST_URL, json={"email": EMAIL}).status_code for _ in range(4)]
        assert statuses == [200, 200, 200, 429]
        assert "exceeded" in caplog.text  # the limiter did log, so the check below means something
        assert EMAIL not in caplog.text


class TestPasswordResetIpKey:
    @pytest.mark.parametrize(
        "forwarded, expected",
        [
            ("203.0.113.7", "203.0.113.7"),
            ("203.0.113.7:51234", "203.0.113.7"),
            ("1.1.1.1, 203.0.113.7:51234", "203.0.113.7"),
            ("203.0.113.7, 198.51.100.2", "198.51.100.2"),
            ("203.0.113.7, ", "203.0.113.7"),
            ("[2001:db8::1]:443", "2001:db8::1"),
            ("[2001:db8::1]", "2001:db8::1"),
            ("2001:db8::1", "2001:db8::1"),
            ("::ffff:203.0.113.7", "::ffff:203.0.113.7"),
            (",", "198.51.100.9"),
            (" , ,", "198.51.100.9"),
            (":80", "198.51.100.9"),
            ("", "198.51.100.9"),
        ],
    )
    def test_key(self, app, forwarded, expected):
        headers = {"X-Forwarded-For": forwarded} if forwarded else {}
        with app.test_request_context("/", headers=headers, environ_base={"REMOTE_ADDR": "198.51.100.9"}):
            assert routes.password_reset_ip_key() == expected

    def test_no_header_falls_back_to_the_socket_address(self, app):
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": "198.51.100.9"}):
            assert routes.password_reset_ip_key() == "198.51.100.9"

    def test_key_is_never_empty(self, app):
        with app.test_request_context("/", headers={"X-Forwarded-For": ","}, environ_base={"REMOTE_ADDR": ""}):
            assert routes.password_reset_ip_key() == "unknown"

    def test_display_helper_is_unchanged(self, app):
        with app.test_request_context("/", headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.7"}):
            assert routes._get_client_ip() == "1.1.1.1"


class TestBoundedBackgroundWork:
    @pytest.fixture()
    def slots(self, monkeypatch):
        semaphore = threading.BoundedSemaphore(1)
        monkeypatch.setattr(routes, "_password_reset_slots", semaphore)
        return semaphore

    @pytest.fixture()
    def worker(self, monkeypatch):
        recording = RecordingWorker()
        monkeypatch.setattr(routes, "_password_reset_worker", recording)
        return recording

    def test_full_queue_drops_the_job_with_the_same_reply(self, client, stores, slots, worker, monkeypatch, caplog):
        job = MagicMock()
        monkeypatch.setattr(routes, "_process_password_reset", job)
        caplog.set_level(logging.INFO)

        accepted = client.post(REQUEST_URL, json={"email": EMAIL})
        assert len(worker.jobs) == 1
        dropped = client.post(REQUEST_URL, json={"email": EMAIL})

        assert len(worker.jobs) == 1
        assert dropped.status_code == accepted.status_code == 200
        assert dropped.get_data() == accepted.get_data()
        assert sorted(dropped.headers.keys()) == sorted(accepted.headers.keys())
        assert "dropped" in caplog.text
        assert EMAIL not in caplog.text
        job.assert_not_called()

    def test_a_freed_slot_accepts_the_next_request(self, client, stores, slots, worker, monkeypatch):
        monkeypatch.setattr(routes, "_process_password_reset", MagicMock())
        client.post(REQUEST_URL, json={"email": EMAIL})
        fn, args = worker.jobs[0]
        fn(*args)
        client.post(REQUEST_URL, json={"email": EMAIL})
        assert len(worker.jobs) == 2

    @pytest.mark.parametrize("outcome", ["success", "failure"])
    def test_slot_is_held_while_pending_and_released_when_the_job_ends(
        self, client, stores, slots, worker, monkeypatch, outcome
    ):
        job = MagicMock(side_effect=RuntimeError("boom") if outcome == "failure" else None)
        monkeypatch.setattr(routes, "_process_password_reset", job)
        client.post(REQUEST_URL, json={"email": EMAIL})
        assert not slots.acquire(blocking=False)

        fn, args = worker.jobs[0]
        if outcome == "failure":
            with pytest.raises(RuntimeError):
                fn(*args)
        else:
            fn(*args)

        job.assert_called_once_with(EMAIL)
        assert slots.acquire(blocking=False)

    def test_slot_is_released_when_the_job_cannot_be_queued(self, client, stores, slots, monkeypatch):
        failing = MagicMock()
        failing.submit.side_effect = RuntimeError("pool is shut down")
        monkeypatch.setattr(routes, "_password_reset_worker", failing)
        resp = client.post(REQUEST_URL, json={"email": EMAIL})
        assert resp.status_code == 200
        assert slots.acquire(blocking=False)

    @pytest.mark.parametrize("outcome", ["success", "failure"])
    def test_slot_is_released_on_the_real_worker(self, client, stores, slots, monkeypatch, outcome):
        done = threading.Event()

        def job(_email):
            done.set()
            if outcome == "failure":
                raise RuntimeError("boom")

        monkeypatch.setattr(routes, "_process_password_reset", job)
        assert client.post(REQUEST_URL, json={"email": EMAIL}).status_code == 200
        assert done.wait(5)
        assert wait_until(lambda: slots.acquire(blocking=False))
