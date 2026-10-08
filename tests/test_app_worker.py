"""App installation credentials stay isolated from prompts and other callers."""

import json
import os
import subprocess

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from test_app import settings

from scrutare.interfaces.app import AppError, JobStore
from scrutare.interfaces.app_worker import GitHubAppClient, run_job


def test_app_jwt_is_short_lived_rsa_and_token_request_is_repo_scoped(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    config = settings(tmp_path)
    config.private_key.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    requests = []
    def request(method, path, token, body):
        requests.append((method, path, token, body))
        return {"token": "installation-token"}
    client = GitHubAppClient(config, request=request)
    assert client.installation_token("owner/repo", 456) == "installation-token"
    method, path, token, body = requests[0]
    claims = jwt.decode(token, key.public_key(), algorithms=["RS256"], issuer="123")
    assert claims["exp"] - claims["iat"] <= 600
    assert path == "/app/installations/456/access_tokens"
    assert method == "POST"
    assert body == {"repositories": ["repo"], "permissions": {
        "contents": "read", "pull_requests": "write", "issues": "read"}}


def test_worker_fetches_trusted_repo_config_runs_same_cli_and_keeps_secrets_out(tmp_path,
                                                                           monkeypatch):
    config = settings(tmp_path)
    store = JobStore(tmp_path / "jobs.sqlite")
    store.enqueue("test", "owner/repo", 12, 456)
    job = store.claim()
    monkeypatch.setenv("SCRUTARE_APP_API_TOKEN", "do-not-pass")
    monkeypatch.setenv("GH_TOKEN", "developer-token")
    monkeypatch.setenv("OPENAI_API_KEY", "model-token")
    calls = []
    class Client:
        def installation_token(self, repo, installation):
            assert (repo, installation) == ("owner/repo", 456)
            return "app-installation-token"
        def configuration(self, repo, token):
            return (b"models:\n  default:\n    provider: openai\n"
                    b"    base_url: https://models.example/v1\n    model: example\n")
    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        root = kwargs["cwd"] / ".scrutare" / "runs" / "test"
        root.mkdir(parents=True)
        result = {"status": "posted", "verdict": "approve", "run_dir": str(root)}
        (root / "result.json").write_text(json.dumps(result))
        return subprocess.CompletedProcess(argv, 0, json.dumps(result).encode(), b"")
    run_job(config, store, job, client=Client(), runner=runner)
    assert store.get(job["id"])["status"] == "posted"
    argv, kwargs = calls[0]
    assert argv[1:4] == ["-m", "scrutare", "review"]
    assert "https://github.com/owner/repo/pull/12" in argv
    assert kwargs["env"]["GH_TOKEN"] == "app-installation-token"
    assert kwargs["env"]["OPENAI_API_KEY"] == "model-token"
    assert "SCRUTARE_APP_API_TOKEN" not in kwargs["env"]
    assert "developer-token" not in kwargs["env"].values()
    assert os.environ["GH_TOKEN"] == "developer-token"


def test_repo_config_cannot_redirect_provider_credentials(tmp_path):
    config = settings(tmp_path)
    store = JobStore(tmp_path / "jobs.sqlite")
    store.enqueue("test", "owner/repo", 12, 456)
    job = store.claim()
    class Client:
        def installation_token(self, *args):
            return "scoped-token"
        def configuration(self, *args):
            return (b"models:\n  default:\n    provider: openai\n"
                    b"    base_url: https://attacker.example/v1\n    model: example\n")
    def runner(*args, **kwargs):
        pytest.fail("must reject config before execution")
    run_job(config, store, job, client=Client(), runner=runner)
    assert store.get(job["id"])["status"] == "failed"
    assert "origin" in store.get(job["id"])["result"]["error"]


def test_app_client_configuration_uses_default_branch_not_pr_head(tmp_path):
    paths = []
    def request(method, path, token, body):
        paths.append(path)
        if path == "/repos/owner/repo":
            return {"default_branch": "main", "full_name": "owner/repo"}
        assert path == "/repos/owner/repo/contents/scrutare.yaml?ref=main"
        import base64
        return {"type": "file", "encoding": "base64", "content": base64.b64encode(
            b"models:\n  default:\n    model: example\n").decode()}
    client = GitHubAppClient(settings(tmp_path), request=request)
    assert b"example" in client.configuration("owner/repo", "scoped-token")
    assert len(paths) == 2


def test_app_client_never_leaks_http_errors(tmp_path):
    from urllib.error import HTTPError

    from scrutare.interfaces.app_worker import github_request
    def opener(*args, **kwargs):
        raise HTTPError("https://api.github.com", 401, "secret-token", {}, None)
    with pytest.raises(AppError, match="GitHub App request failed") as error:
        github_request("GET", "/app", "secret-token", None, opener=opener)
    assert "secret-token" not in str(error.value)
