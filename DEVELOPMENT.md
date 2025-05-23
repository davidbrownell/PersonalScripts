# Local Development

## Enlistment

| Step | Command Line | Additional Information |
| --- | --- | --- |
| 1. Clone the repository locally. | `git clone https://github.com/davidbrownell/PersonalScripts` | https://git-scm.com/docs/git-clone |
| 2. Install [uv](https://github.com/astral-sh/uv). | `curl -LsSf https://astral.sh/uv/install.sh \| sh` on macOS and Linux or <br/>`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` on Windows | https://docs.astral.sh |
| 3. Install dependencies. | `uv sync` | https://docs.astral.sh/uv/concepts/projects/sync |
| 4. Install [pre-commit](https://pre-commit.com/) hooks | `uv run pre-commit install` | https://pre-commit.com/#1-install-pre-commit |

## Development Activities

| Activity | Command Line | Description | Used During Local Development | Invoked by Continuous Integration |
| --- | --- | --- | :-: | :-: |
| Code Formatting | `uv run ruff format` or<br>`uv run ruff format --check` | Format source code using [ruff](https://github.com/astral-sh/ruff) based on settings in `pyproject.toml`. | :white_check_mark: |  |
| Static Code Analysis | `uv run ruff check` | Validate source code using [ruff](https://github.com/astral-sh/ruff) based on settings in `pyproject.toml`. | :white_check_mark: | |
| Run pre-commit scripts | `uv run pre-commit run` | Run [pre-commit](https://pre-commit.com/) scripts based on settings in `.pre-commit-config.yaml`. | :white_check_mark: | |
| Automated Testing | `uv run pytest` or<br/>`uv run pytest --no-cov` | Run automated tests using [pytest](https://docs.pytest.org/) and extract code coverage using [coverage](https://coverage.readthedocs.io/) based on settings in `pyproject.toml`. | :white_check_mark: | |

## Contributing Changes
Pull requests are preferred, since they are specific. For more about how to create a pull request, see https://help.github.com/articles/using-pull-requests/.

We recommend creating different branches for different (logical) changes, and creating a pull request into the `main` branch when you're done. For more information on creating branches, please see https://help.github.com/articles/creating-and-deleting-branches-within-your-repository/.
