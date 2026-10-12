"""Reading YAML files through Jinja2.

Every config and test list goes through YamlTemplate:

  1. full-line '#' comments are blanked (so {{ }} inside a comment is ignored),
  2. Jinja2 renders the text ({{ var }}, {% if %}, {% for %}, {% include %}),
  3. the result is parsed as YAML with MergingLoader.

The `variables:` section is resolved first, by rendering the file a few times
until the variables stop changing, so variables may use each other.
"""

import os
import re
from pathlib import Path

import jinja2
import yaml

from .constants import MAX_VARIABLE_PASSES
from .errors import FlowError, YamlSyntaxError
from .utils import deep_merge


class MergingLoader(yaml.SafeLoader):
    """YAML loader where a top-level key that appears twice is deep-merged.

    Plain YAML keeps only the last copy of a repeated key. Here, when a
    project file includes a master file, both can have `variables:`,
    `phases:`, `models:`, `sections:` ... and they are merged, with the later
    one (the including file) winning. Only the top level merges; nested
    mappings and `<<:` merge keys keep the normal YAML rules.
    """

    def construct_document(self, node):
        self._root = node                       # remember the top-level mapping
        return super().construct_document(node)

    def construct_mapping(self, node, deep=False):
        if node is not getattr(self, "_root", None):
            return super().construct_mapping(node, deep)
        self.flatten_mapping(node)              # applies `<<:` merge keys
        out = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=True)
            value = self.construct_object(value_node, deep=True)
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = deep_merge(out[key], value)
            else:
                out[key] = value
        return out


# A line that holds only a YAML comment. Blanked (not removed) so that line
# numbers in error messages still match the file.
_COMMENT_LINE = re.compile(r"^[ \t]*#.*$", re.M)


def strip_comment_lines(text):
    """Blank full-line comments so Jinja never sees {{ }} / {% %} written in them."""
    return _COMMENT_LINE.sub("", text)


class CommentStrippingLoader(jinja2.FileSystemLoader):
    """Jinja loader for {% include %} that also strips comment lines."""

    def get_source(self, environment, template):
        source, filename, uptodate = super().get_source(environment, template)
        return strip_comment_lines(source), filename, uptodate


def include_search_path(file_dir, include_dirs=()):
    """Directories searched by {% include 'name' %}, in order:

    the including file's directory, -I/--include-dir directories,
    $DV_FLOW_INCLUDE (os.pathsep-separated), every parent directory of the
    including file (nearest first), then the current directory.
    """
    env_dirs = [d for d in os.environ.get("DV_FLOW_INCLUDE", "").split(os.pathsep) if d]
    dirs = [file_dir, *[Path(d).resolve() for d in [*include_dirs, *env_dirs]], *file_dir.parents, Path.cwd()]
    out = []
    for d in dirs:
        if str(d) not in out:
            out.append(str(d))
    return out


class YamlTemplate:
    """One Jinja2 + YAML file (a config or a test list)."""

    def __init__(self, path, include_dirs=()):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FlowError(f"file not found: {self.path}")
        self.source = strip_comment_lines(self.path.read_text(encoding="utf-8"))
        loader = CommentStrippingLoader(include_search_path(self.path.parent, include_dirs))
        # trim_blocks / lstrip_blocks: a {% ... %} line leaves no blank line or indentation behind.
        common = dict(loader=loader, keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True)
        # strict: an undefined name is an error (used for the real render).
        self.strict = jinja2.Environment(undefined=jinja2.StrictUndefined, **common)
        # lenient: undefined names render as '' (only used while resolving `variables:`).
        self.lenient = jinja2.Environment(undefined=jinja2.ChainableUndefined, **common)

    def _parse(self, env, ctx):
        """Render with `env` and context `ctx`, then parse the YAML."""
        try:
            text = env.from_string(self.source).render(**ctx)
        except jinja2.TemplateError as exc:
            line = f":{exc.lineno}" if getattr(exc, "lineno", None) else ""
            raise FlowError(f"{self.path}{line}: jinja error: {exc}") from None
        except RecursionError:
            raise FlowError(f"{self.path}: include loop (a file includes itself, directly or indirectly)") from None
        try:
            data = yaml.load(text, Loader=MergingLoader) or {}
        except yaml.YAMLError as exc:
            raise YamlSyntaxError(f"{self.path}: YAML error after jinja rendering:\n{exc}") from None
        if not isinstance(data, dict):
            raise FlowError(f"{self.path}: top level must be a mapping")
        return data

    def render(self, ctx, overrides=None):
        """Render the file. Returns (data, context).

        ctx        built-in names (model, test, seed, env, ...)
        overrides  -D/--define values; they beat `variables:`
        The returned context = ctx + variables + overrides, and is used to
        render job names and phase args with the same names.
        """
        overrides = overrides or {}
        variables = {}
        # Resolve `variables:` leniently until stable: pass 1 sees no variables,
        # pass 2 sees pass 1's values, ... so `b: "{{ a }}/x"` works in any order.
        for _ in range(MAX_VARIABLE_PASSES):
            try:
                data = self._parse(self.lenient, {**ctx, **variables, **overrides})
            except YamlSyntaxError:
                raise
            except (FlowError, TypeError, ValueError):
                break  # let the strict pass below report the real error
            new = data.get("variables") or {}
            if new == variables:
                break
            variables = new
        data = self._parse(self.strict, {**ctx, **variables, **overrides})
        variables = data.get("variables") or {}
        full = {**ctx, **variables, **overrides, "variables": variables}
        return data, full

    def render_string(self, text, ctx):
        """Render one string (a job name, a phase-args value) with `ctx`."""
        try:
            return self.strict.from_string(str(text)).render(**ctx)
        except jinja2.TemplateError as exc:
            raise FlowError(f"cannot render '{text}': {exc}") from None
