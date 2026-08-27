# Copilot Instructions for python-edubag

## Project Overview

EduBag is a Python package that provides tools for Bootstrapping, Aggregating, and Gathering learner data from various educational platforms. The package includes integrations with platforms like Gradescope, Brightspace, Albert, EdStem, and Gmail to help educators manage and analyze student data.

## Technology Stack

- **Python**: Requires Python 3.13+
- **Package Manager**: Uses `uv` for dependency management
- **CLI Framework**: Typer for command-line interface
- **Key Dependencies**:
  - `loguru` for logging
  - `rich` for terminal output formatting
  - `pandas` for data manipulation
  - `playwright` for browser automation
  - `bs4` (Beautiful Soup 4) for HTML parsing
  - `pyyaml` for configuration
  - `python-dotenv` for environment variables

## Development Workflow

### Task Runner
The project uses `justfile` for task automation. Common commands:
- `just qa` - Run formatting, linting, and tests
- `just test` - Run tests
- `just coverage` - Generate test coverage reports
- `just build` - Build the package

### Linting and Formatting
- **ruff**: Primary linter and formatter (configured in pyproject.toml)
  - Line length: 120 characters
  - Enabled rules: pycodestyle (E/W), Pyflakes (F), isort (I), flake8-bugbear (B), pyupgrade (UP)
- **ty**: Type checker (similar to mypy/pyright)

### Testing
- **Framework**: pytest
- **Test Location**: `/tests` directory
- **Running Tests**:
  - `just test` - Run all tests (using Python 3.13)
  - `just test path/to/test.py` - Run specific test file
  - `just pdb` - Run tests with debugger on failure
  - `just testall` - Test across Python 3.10-3.13 (note: pyproject.toml specifies 3.13+ as minimum, so older versions may not be officially supported)

## Code Style and Conventions

### General Guidelines
1. **Minimal Comments**: Don't add comments unless they match existing style or explain complex logic
2. **Type Hints**: Use type hints where appropriate (enforced by ty)
3. **Import Order**: Managed by ruff with isort rules
4. **Line Length**: Maximum 120 characters
5. **Error Handling**: Use loguru for logging errors and warnings

### Project Structure
```
src/edubag/
├── __init__.py          # Main Typer app
├── __main__.py          # Entry point
├── albert/              # Albert platform integration
├── brightspace/         # Brightspace integration
├── edstem/             # EdStem integration
├── gmail/              # Gmail integration
├── gradescope/         # Gradescope integration
├── aggregator.py       # Data aggregation logic
├── sources.py          # Data source definitions
└── transformers.py     # Data transformation utilities
```

### CLI Command Pattern
- The project uses Typer for CLI commands
- Subcommands are registered through module imports in `__init__.py`
- Each integration module can define its own Typer app that gets included

### Testing Patterns
- Use pytest fixtures for reusable test components
- Test files are named `test_*.py`
- Tests should be placed in the `/tests` directory
- Mock external API calls when testing integrations

## Making Changes

### Before Making Changes
1. Run `just qa` to ensure the codebase is in good state
2. Understand the existing code structure and patterns
3. Check if there are existing tests that need to be updated

### After Making Changes
1. Format code: `just qa` handles formatting automatically
2. Run tests: `just test` to verify changes
3. Check types: Included in `just qa`
4. Build package: `just build` to verify packaging

### Adding New Features
- New platform integrations should go in their own subdirectory under `src/edubag/`
- Register new CLI commands by importing the module in `__init__.py`
- Add corresponding tests in `/tests`
- Update documentation in `/docs` if adding user-facing features

## Package Management
- **Primary Tool**: `uv` is the primary package manager
- Dependencies are defined in `pyproject.toml`
- Use `uv` commands for package management (e.g., `uv run`, `uv build`)
- Legacy `Pipfile` and `Pipfile.lock` may exist but `uv` is preferred for all operations

## Documentation
- Main documentation is in `/docs` directory (index.md, installation.md, usage.md)
- Hosted on ReadTheDocs
- Update relevant docs when adding new features

## Common Issues to Avoid
1. Don't remove or modify working code unless absolutely necessary
2. Don't add new linting/testing tools - use existing ruff and pytest setup
3. Don't change Python version requirements without good reason
4. Ensure all code works with Python 3.13+ (as specified in pyproject.toml)
5. Don't commit temporary files or build artifacts (check .gitignore)

## Security Considerations
- Never commit API keys or credentials
- Use environment variables (python-dotenv) for sensitive configuration
- Be cautious with browser automation (playwright) - ensure proper error handling
