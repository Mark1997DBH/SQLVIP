"""Dameng (达梦 DM8) support for SQLGlot.

DM8's SQL language is close to Oracle, which is why the dialect builds on
SQLGlot's Oracle dialect. The overrides below keep DM's wider numeric type
support and make the generated pagination explicit and Oracle-compatible.
"""

from __future__ import annotations

from sqlglot import exp

from app.dialects.oracle import Oracle as OracleDialect


class Dameng(OracleDialect):
    """Dameng DM8 dialect, based on the Oracle-compatible SQL surface."""

    class Tokenizer(OracleDialect.Tokenizer):
        # DM accepts both standard double-quoted identifiers and MySQL-style
        # backticks in its compatibility modes.
        IDENTIFIERS = ['"', "`"]

    class Generator(OracleDialect.Generator):
        # DM8 supports the SQL-standard OFFSET/FETCH pagination form and the
        # Oracle-compatible FETCH FIRST form. Keep it explicit and portable.
        LIMIT_FETCH = "FETCH"

        def autoincrementcolumnconstraint_sql(
            self, _: exp.AutoIncrementColumnConstraint
        ) -> str:
            return "IDENTITY(1,1)"

        TYPE_MAPPING = {
            **OracleDialect.Generator.TYPE_MAPPING,
            exp.DataType.Type.TINYINT: "TINYINT",
            exp.DataType.Type.SMALLINT: "SMALLINT",
            exp.DataType.Type.INT: "INT",
            exp.DataType.Type.BIGINT: "BIGINT",
            exp.DataType.Type.DOUBLE: "DOUBLE",
            exp.DataType.Type.DATETIME: "TIMESTAMP",
            exp.DataType.Type.TEXT: "CLOB",
            exp.DataType.Type.VARCHAR: "VARCHAR2",
            exp.DataType.Type.NVARCHAR: "NVARCHAR2",
            exp.DataType.Type.BINARY: "BLOB",
            exp.DataType.Type.VARBINARY: "BLOB",
        }
