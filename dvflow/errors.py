"""Exceptions shown to the user.

Anything that is the user's to fix (a typo in a YAML file, an unknown model,
a bad option) raises FlowError with a readable message. cli.main() catches it,
prints "dv_flow: error: <message>" and exits with code 2, without a traceback.
"""


class FlowError(Exception):
    """User-facing configuration or runtime error."""


class YamlSyntaxError(FlowError):
    """A file is not valid YAML after Jinja rendering.

    Kept separate from FlowError so that the variable-resolution passes in
    templates.YamlTemplate.render() can report it at once instead of retrying.
    """
