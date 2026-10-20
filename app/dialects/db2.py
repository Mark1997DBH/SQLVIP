"""IBM Db2 support for SQLGlot.

SQLGlot ships with strong support for the five target databases in SQLVIP,
except Db2 and Dameng. This module fills the Db2 gaps that matter for common
application SQL: pagination, types, date/time special registers, a handful of
Db2 functions and the mandatory dummy table for FROM-less SELECTs.
"""

from __future__ import annotations

import typing as t

from sqlglot import exp, generator, parser, transforms
from sqlglot.dialects.dialect import (
    Dialect,
    no_ilike_sql,
    rename_func,
    trim_sql,
)
from sqlglot.helper import seq_get


def _add_sysibm_dual(expression: exp.Select) -> exp.Select:
    """Db2 requires a FROM clause; add SYSIBM.SYSDUMMY1 when none is present."""
    if not expression.args.get("from_") and not expression.args.get("into"):
        expression.set(
            "from_",
            exp.From(
                this=exp.Table(
                    this=exp.Identifier(this="SYSDUMMY1", quoted=False),
                    db=exp.Identifier(this="SYSIBM", quoted=False),
                )
            ),
        )
    return expression


def _date_add_sql(kind: str) -> t.Callable[[generator.Generator, exp.Expression], str]:
    """Render Db2's ``date + n DAY`` date arithmetic."""

    def _render(self: generator.Generator, expression: exp.Expression) -> str:
        this = self.sql(expression, "this")
        value = self._simplify_unless_literal(expression.expression)
        unit = (expression.args.get("unit") or exp.var("DAY")).name.upper()
        if not unit.endswith("S"):
            unit = f"{unit}S"

        if isinstance(value, exp.Literal):
            raw_value = value.this
        else:
            raw_value = self.sql(value)

        return f"{this} {kind} {raw_value} {unit}"

    return _render


class DB2(Dialect):
    """A pragmatic Db2 dialect for SQL transpilation."""

    NULL_ORDERING = "nulls_are_large"
    TYPED_DIVISION = True
    NORMALIZE_FUNCTIONS = "upper"

    class Parser(parser.Parser):
        FUNCTIONS = {
            **parser.Parser.FUNCTIONS,
            "CHAR": exp.Cast.from_arg_list,
            "DAYOFWEEK": lambda args: exp.Extract(
                this=exp.var("DAYOFWEEK"), expression=seq_get(args, 0)
            ),
            "DAYOFYEAR": lambda args: exp.Extract(
                this=exp.var("DAYOFYEAR"), expression=seq_get(args, 0)
            ),
            "POSSTR": lambda args: exp.StrPosition(
                this=seq_get(args, 0), substr=seq_get(args, 1)
            ),
            "VARCHAR_FORMAT": lambda args: exp.TimeToStr(
                this=seq_get(args, 0), format=seq_get(args, 1)
            ),
        }

    class Generator(generator.Generator):
        LIMIT_FETCH = "FETCH"
        JOIN_HINTS = False
        TABLE_HINTS = False
        QUERY_HINTS = False
        NVL2_SUPPORTED = False
        LAST_DAY_SUPPORTS_DATE_PART = False

        # Db2 folds unquoted identifiers and function names to upper case.
        # SQLGlot keeps the original spelling unless explicitly told otherwise;
        # that is friendlier when migrating quoted/camelCase schemas.
        IDENTIFIERS_CAN_START_WITH_DIGIT = True
        CONCAT_COALESCE = True

        TYPE_MAPPING = {
            **generator.Generator.TYPE_MAPPING,
            # Db2 10.5/11.1 compatibility: map boolean columns to SMALLINT and
            # render TRUE/FALSE literals as 1/0 below.
            exp.DataType.Type.BOOLEAN: "SMALLINT",
            exp.DataType.Type.INT: "INTEGER",
            exp.DataType.Type.TINYINT: "SMALLINT",
            exp.DataType.Type.BINARY: "BLOB",
            exp.DataType.Type.VARBINARY: "BLOB",
            exp.DataType.Type.TEXT: "CLOB",
            exp.DataType.Type.NCHAR: "NCHAR",
            exp.DataType.Type.NVARCHAR: "NVARCHAR",
            exp.DataType.Type.TIMESTAMPTZ: "TIMESTAMP",
            exp.DataType.Type.DATETIME: "TIMESTAMP",
            exp.DataType.Type.UUID: "CHAR(36)",
        }

        TRANSFORMS = {
            **generator.Generator.TRANSFORMS,
            exp.Boolean: lambda self, e: "1" if e.this else "0",
            exp.CurrentDate: lambda self, e: "CURRENT DATE",
            exp.CurrentTime: lambda self, e: "CURRENT TIME",
            exp.CurrentTimestamp: lambda self, e: "CURRENT TIMESTAMP",
            exp.DateAdd: _date_add_sql("+"),
            exp.DateSub: _date_add_sql("-"),
            exp.DateDiff: lambda self, e: (
                f"{self.func('DAYS', e.this)} - {self.func('DAYS', e.expression)}"
            ),
            exp.DateStrToDate: lambda self, e: f"DATE({self.sql(e, 'this')})",
            exp.ILike: no_ilike_sql,
            exp.StrPosition: rename_func("POSSTR"),
            exp.StrToTime: lambda self, e: f"TIMESTAMP({self.sql(e, 'this')})",
            exp.TimeToStr: rename_func("VARCHAR_FORMAT"),
            exp.ToChar: lambda self, e: self.function_fallback_sql(e),
            exp.Trim: trim_sql,
            exp.Select: transforms.preprocess([_add_sysibm_dual]),
        }

        def autoincrementcolumnconstraint_sql(
            self, _: exp.AutoIncrementColumnConstraint
        ) -> str:
            return "GENERATED BY DEFAULT AS IDENTITY"

        def offset_sql(self, expression: exp.Offset) -> str:
            self._vip_has_offset = True
            return f"{super().offset_sql(expression)} ROWS"

        def fetch_sql(self, expression: exp.Fetch) -> str:
            direction = "NEXT" if getattr(self, "_vip_has_offset", False) else (
                expression.args.get("direction") or "FIRST"
            )
            count = self.sql(expression, "count")
            body = (
                f"FETCH {direction} {count} ROWS ONLY"
                if count
                else f"FETCH {direction} ROW ONLY"
            )
            return self.seg(body)

        def extract_sql(self, expression: exp.Extract) -> str:
            this = self.sql(expression, "this").upper()
            if this in ("DAYOFWEEK", "DAYOFYEAR"):
                return f"{this}({self.sql(expression, 'expression')})"
            return super().extract_sql(expression)

        def cast_sql(
            self, expression: exp.Cast, safe_prefix: t.Optional[str] = None
        ) -> str:
            to_type = expression.to
            if isinstance(to_type, exp.DataType) and to_type.this == exp.DataType.Type.UUID:
                return self.sql(expression.this)
            return super().cast_sql(expression, safe_prefix)
