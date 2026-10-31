from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compose_reads_optional_env_file():
    svc = (ROOT / "deploy/docker-compose.yml").read_text()  # text read, no YAML dependency
    assert "env_file:" in svc and "path: .env" in svc and "required: false" in svc


def test_deploy_copies_env_into_build_tree():
    sh = (ROOT / "deploy/deploy.sh").read_text()
    assert 'cp "$REPO/deploy/.env" "$SRC/deploy/.env"' in sh
    assert sh.index('git -C "$REPO" archive') < sh.index('cp "$REPO/deploy/.env"') < sh.index("docker compose build")
