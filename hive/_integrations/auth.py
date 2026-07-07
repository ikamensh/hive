"""Authentication, workspace bootstrap, and machine enrollment.

One workspace, many users: everyone on the GitHub allow-list becomes a member
on first login (admin by default). A member's role rides the AuthContext so
the API can hold resource providers to their own machines and licenses.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from hive.config.settings import Config
from hive.fleet import stable_machine_id
from hive.models import (
    ROLE_ADMIN,
    ROLE_RESOURCE_PROVIDER,
    Machine,
    User,
    Workspace,
    WorkspaceMembership,
)

SESSION_COOKIE = "hive_session"
SESSION_TTL_S = 30 * 24 * 3600
STATE_TTL_S = 10 * 60
# Enrollment tokens onboard a runner machine: minted from a member's session,
# pasted into `hive enroll` on the laptop. Short-lived — it hands out the
# runner token, and the machine it enrolls is claimed for the minting user.
ENROLL_TTL_S = 60 * 60


@dataclass(frozen=True)
class AuthContext:
    user: User
    workspace: Workspace
    role: str = ROLE_ADMIN

    @property
    def workspace_id(self) -> str:
        return self.workspace.id

    @property
    def is_admin(self) -> bool:
        # Every role except resource_provider edits; legacy "owner" rows count
        # as admin.
        return self.role != ROLE_RESOURCE_PROVIDER


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def ensure_workspace(store, config: Config) -> Workspace:
    workspace = store.get(Workspace, config.workspace_id)
    if workspace:
        return workspace
    return store.put(Workspace(id=config.workspace_id, name=config.workspace_name or "personal"))


def ensure_machine(
    store,
    workspace_id: str,
    *,
    name: str,
    machine_id: str = "",
    hostname: str = "",
    kind: str = "unknown",
    machine_type: str = "",
    machine_os: str = "",
    machine_arch: str = "",
    device_kind: str = "",
) -> Machine:
    machine_id = machine_id or stable_machine_id(name, workspace_id)
    now = time.time()
    machine = store.get(Machine, machine_id)
    if machine is None or machine.workspace_id != workspace_id:
        machine = Machine(
            id=machine_id,
            workspace_id=workspace_id,
            name=name,
            hostname=hostname or name,
            kind=kind,
            machine_type=machine_type,
            os=machine_os,
            arch=machine_arch,
            device_kind=device_kind or "unknown",
            first_seen=now,
            last_seen=now,
        )
    else:
        machine.name = name or machine.name
        machine.hostname = hostname or machine.hostname
        machine.kind = kind or machine.kind
        machine.machine_type = machine_type or machine.machine_type
        machine.os = machine_os or machine.os
        machine.arch = machine_arch or machine.arch
        machine.device_kind = device_kind or machine.device_kind
        machine.last_seen = now
    return store.put(machine)


class AuthManager:
    def __init__(self, store, config: Config) -> None:
        self.store = store
        self.config = config
        self.workspace = ensure_workspace(store, config)
        # Config order is meaningful: the first login is the dev-mode identity,
        # so adding members must never re-attribute a dev-mode install.
        self.allowed_github_order = list(
            dict.fromkeys(
                login.strip().lower()
                for login in config.allowed_github_users.split(",")
                if login.strip()
            )
        )
        self.allowed_github = set(self.allowed_github_order)

    def validate_config(self) -> None:
        if self.config.auth_mode == "github":
            missing = [
                name
                for name, value in {
                    "HIVE_GITHUB_CLIENT_ID": self.config.github_client_id,
                    "HIVE_GITHUB_CLIENT_SECRET": self.config.github_client_secret,
                }.items()
                if not value
            ]
            if missing:
                raise RuntimeError(
                    "GitHub auth is enabled but missing " + ", ".join(missing)
                )

    def _secret(self) -> bytes:
        secret = (
            self.config.auth_secret
            or self.config.github_client_secret
            or ("dev-secret" if self.config.auth_mode == "dev" else "")
        )
        if not secret:
            raise HTTPException(500, "HIVE_AUTH_SECRET or GitHub client secret is required")
        return secret.encode()

    def _sign(self, payload: dict) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        sig = hmac.new(self._secret(), raw, hashlib.sha256).digest()
        return f"{_b64(raw)}.{_b64(sig)}"

    def _verify(self, token: str) -> dict:
        try:
            raw_b64, sig_b64 = token.split(".", 1)
            raw = _unb64(raw_b64)
            sig = _unb64(sig_b64)
        except ValueError as exc:
            raise HTTPException(401, "bad session") from exc
        expected = hmac.new(self._secret(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(401, "bad session")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(401, "bad session") from exc
        if payload.get("exp", 0) < time.time():
            raise HTTPException(401, "session expired")
        return payload

    def _ensure_user(self, github_login: str, display_name: str = "") -> tuple[User, WorkspaceMembership]:
        login = github_login.lower()
        existing = next(
            (u for u in self.store.list(User) if u.github_login.lower() == login),
            None,
        )
        user = existing or User(id=f"github:{login}", github_login=login)
        user.display_name = display_name or user.display_name or github_login
        user.last_seen = time.time()
        self.store.put(user)

        membership = next(
            (
                m
                for m in self.store.list(
                    WorkspaceMembership,
                    workspace_id=self.workspace.id,
                    user_id=user.id,
                )
            ),
            None,
        )
        if membership is None:
            membership = self.store.put(
                WorkspaceMembership(
                    id=f"{self.workspace.id}:{user.id}",
                    workspace_id=self.workspace.id,
                    user_id=user.id,
                    role=ROLE_ADMIN,
                )
            )
        return user, membership

    def dev_context(self) -> AuthContext:
        login = self.allowed_github_order[0] if self.allowed_github_order else "dev"
        user, membership = self._ensure_user(login, login)
        return AuthContext(user=user, workspace=self.workspace, role=membership.role)

    def session_token(self, user: User) -> str:
        return self._sign(
            {
                "typ": "session",
                "sub": user.id,
                "workspace_id": self.workspace.id,
                "exp": time.time() + SESSION_TTL_S,
            }
        )

    def require(self, request: Request) -> AuthContext:
        if self.config.auth_mode == "dev":
            return self.dev_context()
        token = request.cookies.get(SESSION_COOKIE)
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1]
        if not token:
            raise HTTPException(401, "login required")
        payload = self._verify(token)
        if payload.get("typ") != "session" or payload.get("workspace_id") != self.workspace.id:
            raise HTTPException(401, "bad session")
        user = self.store.get(User, payload.get("sub", ""))
        if not user:
            raise HTTPException(401, "unknown user")
        memberships = self.store.list(
            WorkspaceMembership,
            workspace_id=self.workspace.id,
            user_id=user.id,
        )
        if not memberships:
            raise HTTPException(403, "not a workspace member")
        user.last_seen = time.time()
        self.store.put(user)
        return AuthContext(user=user, workspace=self.workspace, role=memberships[0].role)

    def enroll_token(self, user: User) -> str:
        return self._sign(
            {"typ": "enroll", "sub": user.id, "exp": time.time() + ENROLL_TTL_S}
        )

    def verify_enroll(self, token: str) -> str:
        """The user id an enrollment token was minted for; 401 on anything off."""
        payload = self._verify(token)
        if payload.get("typ") != "enroll":
            raise HTTPException(401, "bad enrollment token")
        return payload.get("sub", "")

    def state_token(self) -> str:
        return self._sign({"typ": "oauth_state", "exp": time.time() + STATE_TTL_S})

    def verify_state(self, state: str) -> None:
        payload = self._verify(state)
        if payload.get("typ") != "oauth_state":
            raise HTTPException(400, "bad oauth state")

    def github_start(self) -> RedirectResponse:
        if self.config.auth_mode != "github":
            raise HTTPException(404, "GitHub auth is not enabled")
        self.validate_config()
        from urllib.parse import urlencode

        params = urlencode(
            {
                "client_id": self.config.github_client_id,
                "redirect_uri": f"{self.config.public_url.rstrip('/')}/api/auth/github/callback",
                "scope": "read:user repo",
                "state": self.state_token(),
            }
        )
        return RedirectResponse(f"https://github.com/login/oauth/authorize?{params}")

    def github_callback(self, code: str, state: str) -> RedirectResponse:
        if self.config.auth_mode != "github":
            raise HTTPException(404, "GitHub auth is not enabled")
        self.validate_config()
        self.verify_state(state)
        token_response = httpx.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": self.config.github_client_id,
                "client_secret": self.config.github_client_secret,
                "code": code,
                "redirect_uri": f"{self.config.public_url.rstrip('/')}/api/auth/github/callback",
            },
            headers={"Accept": "application/json"},
            timeout=30.0,
        )
        token_response.raise_for_status()
        access_token = token_response.json().get("access_token", "")
        if not access_token:
            raise HTTPException(401, "GitHub did not return an access token")
        user_response = httpx.get(
            "https://api.github.com/user",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )
        user_response.raise_for_status()
        profile = user_response.json()
        login = str(profile.get("login", "")).lower()
        if login not in self.allowed_github:
            raise HTTPException(403, "GitHub user is not allowed")
        user, _membership = self._ensure_user(login, profile.get("name") or login)
        user.github_access_token = access_token
        self.store.put(user)
        response = RedirectResponse("/")
        response.set_cookie(
            SESSION_COOKIE,
            self.session_token(user),
            max_age=SESSION_TTL_S,
            httponly=True,
            secure=self.config.public_url.startswith("https://"),
            samesite="lax",
        )
        return response

    def github_credentials(self, user: User) -> tuple[str, str]:
        """(github_login, token) for GitHub API — session OAuth token or server fallback."""
        return user.github_login, user.github_access_token or self.config.gh_token.strip()
