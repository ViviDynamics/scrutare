"""Prepare a repository for its first review without accessing GitHub or a model."""

import os
import shlex
import shutil
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml

from scrutare.config import ConfigError, parse_config
from scrutare.engine.github import GitHubError, resolve_pr


def setup_review(pr: str, config: Path, model: str | None, provider: str,
                 base_url: str | None) -> int:
    try:
        if not pr.startswith("https://github.com/"):
            raise ValueError("provide the full GitHub pull request URL for offline setup")
        resolve_pr(pr)
        if not config.exists():
            if not model:
                raise ValueError("supply --model to create scrutare.yaml")
            rail = {"provider": provider, "model": model}
            if base_url:
                rail["base_url"] = base_url
            contents = yaml.safe_dump({"models": {"default": rail}}).encode()
            parse_config(contents)
            # Publish complete bytes exclusively. A concurrent setup or user's file wins.
            with NamedTemporaryFile(dir=config.parent) as new:
                new.write(contents)
                new.flush()
                try:
                    os.link(new.name, config)
                except FileExistsError:
                    pass
        if not config.is_file():
            raise ValueError("provide a regular configuration file")
        parse_config(config.read_bytes())
        nare = shutil.which("nare")
        if nare is None:
            raise ValueError("cannot find nare. Run the release installer and use its launcher")
    except ConfigError:
        print("scrutare: invalid configuration. Check the YAML against the configuration guide.",
              file=sys.stderr)
        return 1
    except OSError:
        print("scrutare: cannot read or create the configuration file.", file=sys.stderr)
        return 1
    except (ValueError, GitHubError) as error:
        print(f"scrutare: {error}", file=sys.stderr)
        return 1
    print(shlex.join([sys.executable, "-m", "scrutare", "review", "--pr", pr,
                      "--config", str(config.absolute()), "--nare-executable",
                      str(Path(nare).absolute())]))
    return 0
