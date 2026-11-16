"""The slice of Typer the runner used, reimplemented on `argparse`.

Typer brings Click and `typing_extensions`, and the plugin must run under any stock
`python3` with no install step. The CLI is ninety commands of argument plumbing, so
rewriting each one by hand would be ninety chances to change a flag nobody noticed. This
shim keeps `cli.py` exactly as written — same decorators, same `Annotated[...]`
signatures — and builds an `argparse` parser tree underneath.

What is supported is what `cli.py` actually used, and nothing more:

    Typer(name=, help=, no_args_is_help=)      app.add_typer(sub, name=)
    @app.command("name", hidden=)              Option(*decls, help=, resolve_path=,
    Argument(*decls, help=, exists=,               min=, exists=, dir_okay=)
        dir_okay=, resolve_path=)              echo() · BadParameter · Exit
    Annotated[str | int | float | bool | Path | X | None, Option(...) | Argument(...)]

Parameter roles follow Typer's rules, because `cli.py` was written against them:

    Annotated[T, Argument(...)]   -> positional
    Annotated[T, Option(...)]     -> option, flag from the decl or derived from the name
    bare, no default              -> positional
    bare, with a default          -> option, flag derived from the name

Exit codes follow Click: 0 on success, 1 from `Exit(1)`, 2 for a usage error, which is
what `BadParameter` raises. `CliRunner` mirrors `typer.testing.CliRunner` closely enough
for the existing tests, mixing stderr into `.output` the way Typer's default did.
"""

from __future__ import annotations

import argparse
import contextlib
import inspect
import io
import sys
import types
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any, Union, get_args, get_origin

__all__ = ["Argument", "BadParameter", "CliRunner", "Exit", "Option", "Typer", "echo"]


class BadParameter(Exception):
    """A usage error. Click exits 2 for these, and so do we.

    `.message` exists because Click's does, and `cli.py` reads it when it catches a
    refusal to report *why* a Task is not ready rather than aborting the whole listing.
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


class Exit(Exception):
    """Stop with an explicit exit code."""

    def __init__(self, code: int = 0) -> None:
        super().__init__(code)
        self.exit_code = code


def echo(message: str = "") -> None:
    print(message)


class _ParamInfo:
    __slots__ = ("decls", "help", "resolve_path", "min", "exists", "dir_okay", "file_okay")

    def __init__(
        self,
        *decls: str,
        help: str | None = None,  # noqa: A002 - mirrors the Typer keyword
        resolve_path: bool = False,
        min: float | None = None,  # noqa: A002 - mirrors the Typer keyword
        exists: bool = False,
        dir_okay: bool = True,
        file_okay: bool = True,
    ) -> None:
        self.decls = decls
        self.help = help
        self.resolve_path = resolve_path
        self.min = min
        self.exists = exists
        self.dir_okay = dir_okay
        self.file_okay = file_okay


class OptionInfo(_ParamInfo):
    pass


class ArgumentInfo(_ParamInfo):
    pass


def Option(*decls: str, **kwargs: Any) -> OptionInfo:  # noqa: N802 - Typer's spelling
    return OptionInfo(*decls, **kwargs)


def Argument(*decls: str, **kwargs: Any) -> ArgumentInfo:  # noqa: N802 - Typer's spelling
    return ArgumentInfo(*decls, **kwargs)


# -- type handling -------------------------------------------------------------------


def _unwrap_optional(hint: Any) -> tuple[Any, bool]:
    """`X | None` -> (X, True); anything else -> (hint, False)."""
    if get_origin(hint) in (Union, types.UnionType):
        args = [arg for arg in get_args(hint) if arg is not type(None)]
        if len(args) == 1:
            return args[0], True
    return hint, False


def _convert(raw: Any, base: Any, name: str, info: _ParamInfo | None) -> Any:
    if raw is None:
        return None
    if base is Path:
        value = Path(str(raw))
        if info is not None and info.resolve_path:
            value = value.resolve()
        if info is not None and info.exists and not value.exists():
            raise BadParameter(f"{name}: {value} does not exist")
        if info is not None and value.exists():
            if not info.dir_okay and value.is_dir():
                raise BadParameter(f"{name}: {value} is a directory")
            if not info.file_okay and value.is_file():
                raise BadParameter(f"{name}: {value} is a file")
        return value
    if base is bool:
        return bool(raw)
    for caster in (int, float):
        if base is caster:
            try:
                value = caster(raw)
            except (TypeError, ValueError) as error:
                raise BadParameter(f"{name}: expected {caster.__name__}, got {raw!r}") from error
            if info is not None and info.min is not None and value < info.min:
                raise BadParameter(f"{name}: must be at least {info.min}")
            return value
    return str(raw)


def _flags(name: str, info: _ParamInfo | None, is_bool: bool) -> tuple[list[str], list[str]]:
    """Return (on-flags, off-flags) for an option.

    A decl of `--discovery/--no-discovery` declares both halves at once, which is how
    `cli.py` spells the two boolean flags that need an explicit negative form.
    """
    dashed = "--" + name.replace("_", "-")
    if info is not None and info.decls:
        on: list[str] = []
        off: list[str] = []
        for decl in info.decls:
            if "/" in decl:
                positive, negative = decl.split("/", 1)
                on.append(positive)
                off.append(negative)
            else:
                on.append(decl)
        if is_bool and not off:
            off.append("--no-" + on[0].lstrip("-"))
        return on, off
    return ([dashed], ["--no-" + dashed[2:]] if is_bool else [])


class _Param:
    """One resolved CLI parameter, ready to become an argparse action."""

    def __init__(self, name: str, hint: Any, default: Any, info: _ParamInfo | None) -> None:
        base, optional = _unwrap_optional(hint)
        self.name = name
        self.base = base
        self.info = info
        self.default = default
        self.has_default = default is not inspect.Parameter.empty
        self.required = not self.has_default and not optional
        self.positional = isinstance(info, ArgumentInfo) or (info is None and not self.has_default)
        self.is_bool = base is bool
        # `list[X]` options repeat (`--intent I001 --intent I002`), as in Typer. Without this a
        # single value arrives as a str and callers iterate it character by character.
        self.is_list = get_origin(base) is list
        self.item = (get_args(base) or (str,))[0] if self.is_list else base

    def add_to(self, parser: argparse.ArgumentParser) -> None:
        helptext = self.info.help if self.info is not None else None
        if self.positional:
            parser.add_argument(
                self.name,
                help=helptext,
                nargs=None if self.required else "?",
                default=None if not self.has_default else self.default,
            )
            return
        on, off = _flags(self.name, self.info, self.is_bool)
        if self.is_bool:
            default = self.default if self.has_default else False
            parser.add_argument(*on, dest=self.name, action="store_true", default=default, help=helptext)
            if off:
                parser.add_argument(*off, dest=self.name, action="store_false", help=argparse.SUPPRESS)
            return
        parser.add_argument(
            *on,
            dest=self.name,
            # `append` extends a list default in place, so the declared default is applied in
            # `value_from` instead of being handed to argparse.
            default=None if self.is_list else (self.default if self.has_default else None),
            action="append" if self.is_list else "store",
            required=self.required,
            help=helptext,
        )

    def value_from(self, namespace: argparse.Namespace) -> Any:
        raw = getattr(namespace, self.name, None)
        if raw is None and self.has_default:
            return self.default
        if self.is_bool:
            return bool(raw)
        if self.is_list:
            return None if raw is None else [_convert(item, self.item, self.name, self.info) for item in raw]
        return _convert(raw, self.base, self.name, self.info)


def _params_of(function: Callable[..., Any]) -> list[_Param]:
    hints = typing.get_type_hints(function, include_extras=True)
    params: list[_Param] = []
    for name, parameter in inspect.signature(function).parameters.items():
        hint = hints.get(name, str)
        info: _ParamInfo | None = None
        if get_origin(hint) is typing.Annotated:
            args = get_args(hint)
            hint = args[0]
            info = next((item for item in args[1:] if isinstance(item, _ParamInfo)), None)
        params.append(_Param(name, hint, parameter.default, info))
    return params


class _Command:
    def __init__(self, name: str, function: Callable[..., Any], hidden: bool) -> None:
        self.name = name
        self.function = function
        self.hidden = hidden
        self.help = (function.__doc__ or "").strip().splitlines()[0] if function.__doc__ else None


class Typer:
    """A command group. Nests via `add_typer`, exactly as Typer's does."""

    def __init__(
        self,
        name: str | None = None,
        help: str | None = None,  # noqa: A002
        no_args_is_help: bool = False,
    ) -> None:
        self.name = name
        self.help = help
        self.no_args_is_help = no_args_is_help
        self.commands: dict[str, _Command] = {}
        self.groups: dict[str, Typer] = {}

    def command(self, name: str | None = None, hidden: bool = False) -> Callable[[Callable], Callable]:
        def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
            key = name or function.__name__.replace("_", "-")
            self.commands[key] = _Command(key, function, hidden)
            return function

        return decorate

    def add_typer(self, app: Typer, name: str | None = None) -> None:
        key = name or app.name
        if not key:
            raise ValueError("a nested Typer needs a name")
        self.groups[key] = app

    # -- parsing -------------------------------------------------------------

    def _build(self, parser: argparse.ArgumentParser) -> dict[str, tuple[_Command, list[_Param]]]:
        table: dict[str, tuple[_Command, list[_Param]]] = {}
        subparsers = parser.add_subparsers(dest="_command", metavar="COMMAND")
        for key in sorted(self.commands):
            command = self.commands[key]
            params = _params_of(command.function)
            sub = subparsers.add_parser(key, help=argparse.SUPPRESS if command.hidden else command.help)
            for param in params:
                param.add_to(sub)
            table[key] = (command, params)
        for key in sorted(self.groups):
            group = self.groups[key]
            sub = subparsers.add_parser(key, help=group.help)
            nested = group._build(sub)
            for nested_key, entry in nested.items():
                table[f"{key} {nested_key}"] = entry
            sub.set_defaults(**{f"_group_{key}": True})
        return table

    def __call__(self, argv: list[str] | None = None) -> None:
        args = list(sys.argv[1:] if argv is None else argv)
        parser = argparse.ArgumentParser(prog=self.name or "ai-sdlc", description=self.help)
        table = self._build(parser)
        if not args and self.no_args_is_help:
            parser.print_help()
            raise SystemExit(0)
        namespace = parser.parse_args(args)

        # Rebuild the dotted key argparse consumed across nested subparsers.
        selected = [part for part in args if not part.startswith("-")]
        key = None
        for length in (2, 1):
            candidate = " ".join(selected[:length])
            if candidate in table:
                key = candidate
                break
        if key is None:
            parser.print_help()
            raise SystemExit(0 if self.no_args_is_help else 2)

        command, params = table[key]
        try:
            kwargs = {param.name: param.value_from(namespace) for param in params}
            command.function(**kwargs)
        except BadParameter as error:
            print(f"Error: {error}", file=sys.stderr)
            raise SystemExit(2) from error
        except Exit as error:
            raise SystemExit(error.exit_code) from error
        raise SystemExit(0)


class Result:
    __slots__ = ("exit_code", "output", "exception")

    def __init__(self, exit_code: int, output: str, exception: BaseException | None) -> None:
        self.exit_code = exit_code
        self.output = output
        self.exception = exception

    @property
    def stdout(self) -> str:
        return self.output


class CliRunner:
    """Enough of `typer.testing.CliRunner` for the runner's own tests.

    stderr is mixed into `.output`, which is what Typer's runner did by default and what
    the assertions in `tests/` expect.
    """

    def invoke(self, app: Typer, args: list[str] | None = None) -> Result:
        buffer = io.StringIO()
        code = 0
        raised: BaseException | None = None
        try:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                app(list(args or []))
        except SystemExit as exit_signal:
            code = int(exit_signal.code or 0)
        except BaseException as error:  # noqa: BLE001 - the runner reports, never swallows
            raised = error
            code = 1
        return Result(code, buffer.getvalue(), raised)
