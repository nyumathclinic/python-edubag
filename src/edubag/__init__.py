"""Top-level package for EduBag."""

__author__ = """Matthew Leingang"""
__email__ = 'leingang@nyu.edu'

import typer

app = typer.Typer()

# Import submodules at the end to register their commands
from edubag import (  # noqa: E402
    albert,  # noqa: F401
    gmail,  # noqa: F401
    gradescope,  # noqa: F401
    brightspace,  # noqa: F401
    edstem,  # noqa: F401
    polleverywhere,  # noqa: F401
    webassign,  # noqa: F401
)

if __name__ == "__main__":
    app()
