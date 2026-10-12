"""
Memberships: a user from another organisation (e.g. a CA) works in a client's
books with one login, with the role the client gave them — never their own.
"""
import datetime
import re
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import jwt
import pytest

from tests.conftest import JWT_SECRET, make_token
from smart_invoice_pro.utils.rbac_resolver import resolve_user_permissions

CA_TENANT = "tenant-ca-firm"
CLIENT_TENANT = "tenant-client-1"
OTHER_TENANT = "tenant-other-9"

CA_USER = {
    "id": "user-ca-1", "tenant_id": CA_TENANT, "username": "ca",
    "email": "CA@Example.com", "email_verified": True, "role": "Admin", "is_super_admin": True,
    "password": "hash",
}
CLIENT_ADMIN = {
    "id": "user-client-admin", "tenant_id": CLIENT_TENANT, "username": "owner",
    "email": "owner@client.example", "role": "Admin", "password": "hash",
}
CLIENT_SALES = {
    "id": "user-client-sales", "tenant_id": CLIENT_TENANT, "username": "sales",
    "email": "sales@client.example", "role": "Sales", "password": "hash",
}
UNVERIFIED = {
    "id": "user-unverified", "tenant_id": OTHER_TENANT, "username": "imposter",
    "email": "ca@example.com", "email_verified": False, "role": "Admin", "password": "hash",
}

ROLES = {
    "role-accountant": {"id": "role-accountant", "tenant_id": CLIENT_TENANT, "name": "Accountant",
                        "permissions": {"invoices": {"view": True, "create": False}}},
    "role-admin": {"id": "role-admin", "tenant_id": CLIENT_TENANT, "name": "Admin", "permissions": {}},
}


class FakeContainer:
    """Small in-memory stand-in that understands the queries used here."""

    def __init__(self, items=None):
        self.items = list(items or [])

    def query_items(self, query, parameters=None, enable_cross_partition_query=True):
        p = {x["name"]: x["value"] for x in (parameters or [])}
        q = " ".join(query.split())
        out = []
        for it in self.items:
            if "@id" in p and it.get("id") != p["@id"]:
                continue
            m = re.search(r"c\.id = '([^']*)'", q)
            if m and it.get("id") != m.group(1):
                continue
            if "@tid" in p and it.get("tenant_id") != p["@tid"]:
                continue
            if "status = 'active'" in q and it.get("status") != "active":
                continue
            if "@email" in p:
                email = (it.get("email") or "").lower()
                if "@uid" in p:
                    if not (it.get("user_id") == p["@uid"] or (email == p["@email"] and not it.get("user_id"))):
                        continue
                elif email != p["@email"]:
                    continue
            elif "@uid" in p:
                key = "user_id" if "c.user_id" in q else "id"
                if it.get(key) != p["@uid"]:
                    continue
            out.append(dict(it))
        return out

    def create_item(self, body):
        self.items.append(dict(body))
        return body

    def replace_item(self, item, body):
        self.items = [dict(body) if x["id"] == item else x for x in self.items]
        return body

    def delete_item(self, item, partition_key=None):
        self.items = [x for x in self.items if x["id"] != item]


def _membership(**kw):
    doc = {
        "id": "m-1", "tenant_id": CLIENT_TENANT, "email": "ca@example.com", "user_id": None,
        "role": "Accountant", "role_id": "role-accountant", "status": "active",
    }
    doc.update(kw)
    return doc


@pytest.fixture
def world():
    users = FakeContainer([CA_USER, CLIENT_ADMIN, CLIENT_SALES, UNVERIFIED])
    members = FakeContainer()
    refresh = FakeContainer()
    role_lookup = lambda role_id, tenant_id: (  # noqa: E731
        ROLES.get(role_id) if ROLES.get(role_id, {}).get("tenant_id") == tenant_id else None
    )
    with ExitStack() as stack:
        for target in (
            "smart_invoice_pro.utils.rbac_resolver.users_container",
            "smart_invoice_pro.api.roles_api.users_container",
            "smart_invoice_pro.api.memberships_api.users_container",
            "smart_invoice_pro.api.routes.users_container",
        ):
            stack.enter_context(patch(target, users))
        stack.enter_context(patch("smart_invoice_pro.utils.memberships._container", return_value=members))
        stack.enter_context(patch("smart_invoice_pro.api.routes.refresh_tokens_container", refresh))
        stack.enter_context(patch("smart_invoice_pro.api.roles_permissions_api._get_role_by_id", side_effect=role_lookup))
        stack.enter_context(patch("smart_invoice_pro.utils.rbac_resolver._get_role_by_id", side_effect=role_lookup))
        stack.enter_context(patch("smart_invoice_pro.api.memberships_api.log_audit", MagicMock()))
        stack.enter_context(patch("smart_invoice_pro.api.memberships_api.get_tenant_by_id",
                                  side_effect=lambda tid: {"id": tid, "name": f"Org {tid}"}))
        yield {"users": users, "members": members, "refresh": refresh}


def _h(user, tenant=None, **claims):
    token = make_token(user_id=user["id"], tenant_id=tenant or user["tenant_id"], **claims)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


class TestGrant:
    def test_admin_grants_access_with_a_client_role(self, client, world):
        resp = client.post("/api/settings/memberships", headers=_h(CLIENT_ADMIN),
                           json={"email": " CA@example.com ", "role_id": "role-accountant"})
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["email"] == "ca@example.com" and body["role"] == "Accountant"
        assert "user_id" not in body and "created_by" not in body
        [doc] = world["members"].items
        assert doc["tenant_id"] == CLIENT_TENANT and doc["status"] == "active" and doc["user_id"] is None

    def test_admin_role_cannot_be_given_to_outsiders(self, client, world):
        resp = client.post("/api/settings/memberships", headers=_h(CLIENT_ADMIN),
                           json={"email": "ca@example.com", "role_id": "role-admin"})
        assert resp.status_code == 400
        assert world["members"].items == []

    def test_role_must_belong_to_this_tenant(self, client, world):
        resp = client.post("/api/settings/memberships", headers=_h(CA_USER),
                           json={"email": "x@example.com", "role_id": "role-accountant"})
        assert resp.status_code == 400

    def test_non_admin_cannot_grant(self, client, world):
        resp = client.post("/api/settings/memberships", headers=_h(CLIENT_SALES),
                           json={"email": "ca@example.com", "role_id": "role-accountant"})
        assert resp.status_code == 403

    def test_duplicate_and_own_user_are_refused(self, client, world):
        world["members"].items.append(_membership())
        dup = client.post("/api/settings/memberships", headers=_h(CLIENT_ADMIN),
                          json={"email": "ca@example.com", "role_id": "role-accountant"})
        own = client.post("/api/settings/memberships", headers=_h(CLIENT_ADMIN),
                          json={"email": "sales@client.example", "role_id": "role-accountant"})
        assert dup.status_code == 409 and own.status_code == 409

    def test_revoke_only_inside_own_tenant(self, client, world):
        world["members"].items.append(_membership())
        other = client.delete("/api/settings/memberships/m-1", headers=_h(CA_USER))
        assert other.status_code == 404
        resp = client.delete("/api/settings/memberships/m-1", headers=_h(CLIENT_ADMIN))
        assert resp.status_code == 200
        assert world["members"].items[0]["status"] == "revoked"


class TestMemberSide:
    def test_organizations_lists_home_and_binds_by_verified_email(self, client, world):
        world["members"].items.append(_membership())
        resp = client.get("/api/auth/organizations", headers=_h(CA_USER))
        assert resp.status_code == 200
        orgs = resp.get_json()
        assert [o["tenant_id"] for o in orgs] == [CA_TENANT, CLIENT_TENANT]
        assert orgs[0]["is_home"] and orgs[0]["current"]
        assert orgs[1]["role"] == "Accountant" and not orgs[1]["current"]
        assert world["members"].items[0]["user_id"] == CA_USER["id"]

    def test_unverified_email_cannot_claim_a_membership(self, client, world):
        world["members"].items.append(_membership())
        resp = client.get("/api/auth/organizations", headers=_h(UNVERIFIED))
        assert [o["tenant_id"] for o in resp.get_json()] == [OTHER_TENANT]
        assert world["members"].items[0]["user_id"] is None
        switch = client.post("/api/auth/switch-organization", headers=_h(UNVERIFIED),
                             json={"tenant_id": CLIENT_TENANT})
        assert switch.status_code == 403

    def test_account_without_verified_flag_cannot_claim(self, client, world):
        legacy = dict(CA_USER, id="user-legacy", tenant_id=OTHER_TENANT, is_super_admin=False)
        del legacy["email_verified"]
        world["users"].items.append(legacy)
        world["members"].items.append(_membership())
        resp = client.get("/api/auth/organizations", headers=_h(legacy))
        assert [o["tenant_id"] for o in resp.get_json()] == [OTHER_TENANT]
        assert world["members"].items[0]["user_id"] is None

    def test_fresh_sign_up_with_the_shared_email_cannot_claim(self, client, world):
        world["members"].items.append(_membership())
        with patch("smart_invoice_pro.utils.tenant_service.ensure_tenant_exists"):
            reg = client.post("/api/auth/register",
                              json={"username": "ca@example.com", "password": "secret123"})
        assert reg.status_code == 201
        new_user = reg.get_json()["user"]
        headers = _h(new_user)
        orgs = client.get("/api/auth/organizations", headers=headers)
        assert [o["tenant_id"] for o in orgs.get_json()] == [new_user["tenant_id"]]
        switch = client.post("/api/auth/switch-organization", headers=headers,
                             json={"tenant_id": CLIENT_TENANT})
        assert switch.status_code == 403
        assert world["members"].items[0]["user_id"] is None

    def test_switch_issues_tokens_with_the_membership_role(self, client, world):
        world["members"].items.append(_membership())
        resp = client.post("/api/auth/switch-organization", headers=_h(CA_USER),
                           json={"tenant_id": CLIENT_TENANT})
        assert resp.status_code == 200
        data = resp.get_json()
        payload = jwt.decode(data["access_token"], JWT_SECRET, algorithms=["HS256"])
        assert payload["tenant_id"] == CLIENT_TENANT
        assert payload["membership_id"] == "m-1"
        assert payload["role"] == "Accountant" and payload["is_super_admin"] is False
        [record] = world["refresh"].items
        assert record["tenant_id"] == CLIENT_TENANT and record["membership_id"] == "m-1"

    def test_switch_without_membership_is_refused(self, client, world):
        resp = client.post("/api/auth/switch-organization", headers=_h(CA_USER),
                           json={"tenant_id": CLIENT_TENANT})
        assert resp.status_code == 403
        assert world["refresh"].items == []

    def test_switch_back_home_gives_home_role(self, client, world):
        resp = client.post("/api/auth/switch-organization",
                           headers=_h(CA_USER, CLIENT_TENANT),
                           json={"tenant_id": CA_TENANT})
        # No membership: the token for the client tenant is not accepted for role checks,
        # but switching home only needs a valid login.
        assert resp.status_code == 200
        payload = jwt.decode(resp.get_json()["access_token"], JWT_SECRET, algorithms=["HS256"])
        assert payload["tenant_id"] == CA_TENANT and payload["role"] == "Admin"
        assert "membership_id" not in payload


class TestInsideTheClientBooks:
    def test_home_admin_role_does_not_carry_over(self, client, world):
        world["members"].items.append(_membership(user_id=CA_USER["id"]))
        headers = _h(CA_USER, CLIENT_TENANT, membership_id="m-1")
        assert client.get("/api/settings/memberships", headers=headers).status_code == 403
        assert client.get("/api/my-role", headers=headers).get_json()["role"] == "Accountant"
        is_admin, perms = resolve_user_permissions(CA_USER["id"], CLIENT_TENANT)
        assert is_admin is False
        assert perms == {"invoices": {"view": True, "create": False}}

    def test_no_membership_means_no_permissions(self, client, world):
        is_admin, perms = resolve_user_permissions(CA_USER["id"], CLIENT_TENANT)
        assert (is_admin, perms) == (False, {})

    def test_revoked_membership_ends_the_session_at_once(self, client, world):
        world["members"].items.append(_membership(user_id=CA_USER["id"], status="revoked"))
        headers = _h(CA_USER, CLIENT_TENANT, membership_id="m-1")
        assert client.get("/api/my-role", headers=headers).status_code == 401
        assert resolve_user_permissions(CA_USER["id"], CLIENT_TENANT) == (False, {})

    def test_token_for_someone_elses_membership_is_refused(self, client, world):
        world["members"].items.append(_membership(user_id="someone-else"))
        headers = _h(CA_USER, CLIENT_TENANT, membership_id="m-1")
        assert client.get("/api/my-role", headers=headers).status_code == 401

    def test_refresh_keeps_the_membership_while_active(self, client, world):
        world["members"].items.append(_membership(user_id=CA_USER["id"]))
        world["refresh"].items.append(_refresh_record())
        resp = client.post("/api/auth/refresh", json={"refresh_token": "rt-value"})
        assert resp.status_code == 200
        payload = jwt.decode(resp.get_json()["access_token"], JWT_SECRET, algorithms=["HS256"])
        assert payload["tenant_id"] == CLIENT_TENANT and payload["membership_id"] == "m-1"
        assert payload["role"] == "Accountant" and payload["is_super_admin"] is False
        assert world["refresh"].items[0]["membership_id"] == "m-1"

    def test_refresh_after_revoke_is_refused_and_forgotten(self, client, world):
        world["members"].items.append(_membership(user_id=CA_USER["id"], status="revoked"))
        world["refresh"].items.append(_refresh_record())
        resp = client.post("/api/auth/refresh", json={"refresh_token": "rt-value"})
        assert resp.status_code == 401
        assert world["refresh"].items == []


def _refresh_record():
    return {
        "id": "rt-1", "user_id": CA_USER["id"], "tenant_id": CLIENT_TENANT,
        "membership_id": "m-1", "token": "rt-value",
        "expires_at": (datetime.datetime.utcnow() + datetime.timedelta(days=1)).isoformat(),
    }
