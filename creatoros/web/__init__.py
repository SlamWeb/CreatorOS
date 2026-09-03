"""Local, read-only HTTP surface for the CreatorOS Studio."""


def create_app(*args, **kwargs):
    """Lazy factory so importing the package never opens the default database."""
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)


__all__ = ["create_app"]
