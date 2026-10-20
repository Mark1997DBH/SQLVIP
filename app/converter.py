"""SQL conversion service built on SQLGlot.

The service is intentionally stateless: each request parses the input with the
source dialect, normalises a few cross-dialect constructs, and renders it with
the target dialect.
"""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError, TokenError, UnsupportedError

from app import dialects as _dialects  # noqa: F401  (registers db2/dameng)

logger = logging.getLogger(__name__)

MAX_INPUT_LENGTH = 100_000
MAX_STATEMENTS = 100

ORACLE_LIKE = {"oracle", "dameng"}

DIALECTS: "OrderedDict[str, dict[str, str]]" = OrderedDict(
    [
        ("db2", {"label": "DB2", "vendor": "IBM Db2"}),
        ("postgres", {"label": "PostgreSQL", "vendor": "PostgreSQL"}),
        ("oracle", {"label": "Oracle", "vendor": "Oracle Database"}),
        ("mysql", {"label": "MySQL", "vendor": "MySQL / MariaDB"}),
        ("dameng", {"label": "达梦 DM", "vendor": "达梦数据库 DM8"}),
    ]
)


class ConversionError(Exception):
    """A user-facing conversion failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "CONVERSION_ERROR",
        hint: str | None = None,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint
        self.line = line
        self.column = column

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "line": self.line,
            "column": self.column,
        }


@dataclass(slots=True)
class ConversionResult:
    sql: str
    source: str
    target: str
    statement_count: int
    elapsed_ms: int
    pretty: bool
    input_chars: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sql": self.sql,
            "source": self.source,
            "target": self.target,
            "source_label": DIALECTS[self.source]["label"],
            "target_label": DIALECTS[self.target]["label"],
            "statement_count": self.statement_count,
            "elapsed_ms": self.elapsed_ms,
            "pretty": self.pretty,
            "notes": self.notes,
            "input_chars": self.input_chars,
            "output_chars": len(self.sql),
        }


_STRING_OR_COMMENT = re.compile(
    r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*.*?\*/)",
    re.DOTALL,
)
_DB2_CURRENT = re.compile(r"\bCURRENT\s+(DATE|TIME|TIMESTAMP)\b", re.IGNORECASE)
_DB2_ISOLATION = re.compile(r"\s+\bWITH\s+(UR|CS|RS|RR)\b(?=\s*(?:;|$))", re.IGNORECASE)
_ROWNUM_LIMIT = re.compile(r"\bROWNUM\b", re.IGNORECASE)


def _apply_to_code(sql: str, transform: Any) -> str:
    """Apply a text transform only to SQL code, never strings or comments."""
    parts = _STRING_OR_COMMENT.split(sql)
    for index in range(0, len(parts), 2):
        parts[index] = transform(parts[index])
    return "".join(parts)


def _preprocess_db2(sql: str) -> tuple[str, list[str]]:
    """Translate Db2-only special register spellings and isolation suffixes."""
    notes: list[str] = []

    def transform(segment: str) -> str:
        segment, removed = _DB2_ISOLATION.subn("", segment)
        if removed:
            notes.append(
                "已移除 Db2 的 WITH UR/CS/RS/RR 隔离级别后缀，请在目标库确认事务隔离设置。"
            )
        return _DB2_CURRENT.sub(
            lambda match: f"CURRENT_{match.group(1).upper()}", segment
        )

    return _apply_to_code(sql, transform), list(dict.fromkeys(notes))


def _is_rownum(node: exp.Expression) -> bool:
    return (
        isinstance(node, exp.Column)
        and not node.table
        and node.name.upper() == "ROWNUM"
    )


def _replace_simple_rownum(statement: exp.Expression) -> int:
    """Turn a simple ``WHERE ROWNUM <= n`` into a portable LIMIT clause."""
    converted = 0
    for select in list(statement.find_all(exp.Select)):
        if select.args.get("limit"):
            continue

        where = select.args.get("where")
        if not where or not isinstance(where.this, exp.LTE):
            continue

        predicate = where.this
        limit_value = predicate.expression
        if not _is_rownum(predicate.this) or not isinstance(limit_value, exp.Literal):
            continue
        if not limit_value.is_int or int(limit_value.this) <= 0:
            continue

        select.set("where", None)
        select.set("limit", exp.Limit(expression=exp.Literal.number(limit_value.this)))
        converted += 1

    return converted


def _normalise_functions(statement: exp.Expression) -> None:
    """Normalise vendor functions that SQLGlot leaves as Anonymous calls."""
    for node in list(statement.walk()):
        if not isinstance(node, exp.Anonymous):
            continue

        name = (node.name or "").upper()
        args = node.expressions

        if name in {"NOW", "LOCALTIME", "LOCALTIMESTAMP"}:
            replacement: exp.Expression = (
                exp.CurrentTimestamp(this=args[0].copy()) if args else exp.CurrentTimestamp()
            )
            node.replace(replacement)
        elif name in {"CURDATE", "CURRENT_DATE"}:
            node.replace(exp.CurrentDate())
        elif name in {"CURTIME", "CURRENT_TIME"}:
            node.replace(exp.CurrentTime())
        elif name == "SYSDATE":
            node.replace(exp.CurrentTimestamp(sysdate=True))
        elif name in {"IFNULL", "NVL"} and len(args) >= 2:
            node.replace(
                exp.Coalesce(
                    this=args[0].copy(),
                    expressions=[arg.copy() for arg in args[1:]],
                )
            )


def _rewrite_concat(statement: exp.Expression) -> int:
    """Oracle/达梦 only accept CONCAT(a, b); use || for longer expressions."""
    rewritten = 0
    for concat in list(statement.find_all(exp.Concat)):
        args = concat.expressions
        if not args:
            continue

        if len(args) == 1:
            concat.replace(args[0].copy())
            rewritten += 1
            continue

        if len(args) == 2:
            combined: exp.Expression = exp.DPipe(
                this=args[0].copy(), expression=args[1].copy(), safe=True
            )
        else:
            combined = args[0].copy()
            for arg in args[1:]:
                combined = exp.DPipe(this=combined, expression=arg.copy(), safe=True)

        concat.replace(combined)
        rewritten += 1

    return rewritten


_INTEGER_TYPES = {
    exp.DataType.Type.TINYINT,
    exp.DataType.Type.SMALLINT,
    exp.DataType.Type.INT,
    exp.DataType.Type.MEDIUMINT,
    exp.DataType.Type.BIGINT,
}


def _normalise_integer_types(statement: exp.Expression, target: str) -> None:
    """Remove MySQL display widths and preserve TINYINT(1)'s boolean intent."""
    for data_type in statement.find_all(exp.DataType):
        if data_type.this == exp.DataType.Type.TINYINT and data_type.expressions:
            width = data_type.expressions[0]
            if (
                target == "oracle"
                and isinstance(width, exp.DataTypeParam)
                and isinstance(width.this, exp.Literal)
                and width.this.is_int
                and int(width.this.this) == 1
            ):
                # TINYINT(1) is MySQL's conventional boolean spelling.
                data_type.set("this", exp.DataType.Type.BOOLEAN)
                data_type.set("expressions", [])
                continue

        if data_type.this in _INTEGER_TYPES and data_type.expressions:
            data_type.set("expressions", [])


def _normalise_identity_columns(statement: exp.Expression, target: str) -> int:
    """Make identity columns portable across MySQL, PostgreSQL, Db2 and DM."""
    if target not in {"postgres", "mysql", "db2", "dameng"}:
        return 0

    changed = 0
    for column in statement.find_all(exp.ColumnDef):
        constraints = column.args.get("constraints") or []
        identity_index = next(
            (
                index
                for index, constraint in enumerate(constraints)
                if isinstance(constraint.kind, exp.GeneratedAsIdentityColumnConstraint)
            ),
            None,
        )
        if identity_index is None:
            continue

        data_type = column.args.get("kind")
        if isinstance(data_type, exp.DataType) and data_type.this not in _INTEGER_TYPES:
            column.set("kind", exp.DataType.build("BIGINT"))

        if target in {"mysql", "dameng"}:
            constraints[identity_index] = exp.ColumnConstraint(
                kind=exp.AutoIncrementColumnConstraint()
            )
            column.set("constraints", constraints)

        changed += 1

    return changed


def _add_dual(statement: exp.Expression) -> None:
    """Oracle versions before 23c require DUAL for FROM-less SELECTs."""
    for select in statement.find_all(exp.Select):
        if select.args.get("from_") or select.args.get("into"):
            continue
        select.set(
            "from_",
            exp.From(this=exp.Table(this=exp.Identifier(this="DUAL", quoted=False))),
        )


def _is_dual(table: exp.Table) -> bool:
    return not table.db and table.name.upper() == "DUAL"


def _is_sysdummy(table: exp.Table) -> bool:
    return (table.db or "").upper() == "SYSIBM" and table.name.upper() == "SYSDUMMY1"


_MYSQL_ONLY_TABLE_PROPERTIES = (
    exp.EngineProperty,
    exp.CharacterSetProperty,
    exp.CollateProperty,
    exp.SchemaCommentProperty,
    exp.RowFormatProperty,
    exp.AutoIncrementProperty,
    exp.BlockCompressionProperty,
)


def _strip_mysql_table_properties(statement: exp.Expression, target: str) -> int:
    """Remove MySQL-only table options that break Oracle/PostgreSQL/Db2/DM DDL."""
    if target == "mysql":
        return 0

    removed = 0
    for prop in list(statement.find_all(exp.Property)):
        if isinstance(prop, _MYSQL_ONLY_TABLE_PROPERTIES):
            prop.pop()
            removed += 1
    return removed


def _strip_column_comments(statement: exp.Expression, target: str) -> int:
    """Inline MySQL column comments are not valid in Oracle/Db2/DM DDL."""
    if target == "mysql":
        return 0

    removed = 0
    for constraint in list(statement.find_all(exp.CommentColumnConstraint)):
        constraint.pop()
        removed += 1
    return removed


def _normalise_listagg(statement: exp.Expression, target: str) -> int:
    """Translate Oracle/Db2 LISTAGG to GROUP_CONCAT or STRING_AGG."""
    if target not in {"mysql", "postgres"}:
        return 0

    converted = 0
    for within in list(statement.find_all(exp.WithinGroup)):
        function = within.this
        if not isinstance(function, exp.Anonymous) or function.name.upper() != "LISTAGG":
            continue

        args = function.expressions
        if not args:
            continue

        value = args[0].copy()
        order = within.args.get("expression")
        if isinstance(order, exp.Order):
            value = exp.Order(
                this=value,
                expressions=[item.copy() for item in order.expressions],
            )

        separator = args[1].copy() if len(args) > 1 else exp.Literal.string(",")
        within.replace(exp.GroupConcat(this=value, separator=separator))
        converted += 1

    return converted


def _normalise_dummy_tables(statement: exp.Expression, target: str) -> None:
    """Translate Oracle DUAL and Db2 SYSIBM.SYSDUMMY1 to the target equivalent."""
    for table in list(statement.find_all(exp.Table)):
        dual = _is_dual(table)
        sysdummy = _is_sysdummy(table)
        if not dual and not sysdummy:
            continue

        if target in ORACLE_LIKE and sysdummy:
            table.replace(exp.to_table("DUAL"))
        elif target == "db2" and dual:
            table.replace(exp.to_table("SYSIBM.SYSDUMMY1"))
        elif target == "mysql" and sysdummy:
            table.replace(exp.to_table("DUAL"))
        elif target == "postgres" and (dual or sysdummy) and not table.alias:
            from_clause = table.parent
            select = table.find_ancestor(exp.Select)
            joins = select.args.get("joins") if select else None
            if isinstance(from_clause, exp.From) and not joins:
                # PostgreSQL allows FROM-less SELECT, so the dummy table can
                # simply be removed rather than replaced with an invalid name.
                from_clause.pop()


_SERIAL_TYPES = {
    exp.DataType.Type.SMALLSERIAL: "SMALLINT",
    exp.DataType.Type.SERIAL: "INT",
    exp.DataType.Type.BIGSERIAL: "BIGINT",
}


def _normalise_serial_columns(statement: exp.Expression, target: str) -> int:
    """Expand PostgreSQL SERIAL aliases for targets that do not support them."""
    if target == "postgres":
        return 0

    changed = 0
    for column in statement.find_all(exp.ColumnDef):
        data_type = column.args.get("kind")
        if not isinstance(data_type, exp.DataType) or data_type.this not in _SERIAL_TYPES:
            continue

        column.set("kind", exp.DataType.build(_SERIAL_TYPES[data_type.this]))
        constraints = list(column.args.get("constraints") or [])
        has_identity = any(
            isinstance(
                constraint.kind,
                (exp.AutoIncrementColumnConstraint, exp.GeneratedAsIdentityColumnConstraint),
            )
            for constraint in constraints
        )
        if not has_identity:
            constraints.insert(
                0, exp.ColumnConstraint(kind=exp.AutoIncrementColumnConstraint())
            )
            column.set("constraints", constraints)
        changed += 1

    return changed


def _add_notes_for_known_differences(
    notes: list[str],
    original_sql: str,
    source: str,
    target: str,
    rownum_converted: int,
) -> None:
    if source in ORACLE_LIKE and target not in ORACLE_LIKE:
        remaining_rownum = len(_ROWNUM_LIMIT.findall(original_sql)) - rownum_converted
        if remaining_rownum > 0:
            notes.append(
                "检测到复杂 ROWNUM 分页，已按原样保留；请改用目标库的 LIMIT/FETCH 语法复核。"
            )
        if re.search(r"\bCONNECT\s+BY\b", original_sql, re.IGNORECASE):
            notes.append("检测到层次查询 CONNECT BY，目标数据库可能不支持，请人工改写。")

    if source == "mysql" and target in ORACLE_LIKE and re.search(
        r"\bCONCAT\s*\(", original_sql, re.IGNORECASE
    ):
        notes.append(
            "MySQL 的 CONCAT 遇 NULL 返回 NULL，Oracle/达梦的 || 将 NULL 视为空串；请确认空值语义。"
        )

    if source == "mysql" and "`" in original_sql:
        notes.append(
            "输入包含 MySQL 反引号标识符，已按目标方言改写；请核对大小写敏感的表名和列名。"
        )

    if source == "mysql" and target == "oracle" and re.search(
        r"\bAUTO_INCREMENT\b", original_sql, re.IGNORECASE
    ):
        notes.append(
            "MySQL AUTO_INCREMENT 已改写为 Oracle 12c+ 的 IDENTITY；"
            "若目标库为 11g 及更早版本，请改用序列和触发器。"
        )

    if source == "mysql" and target in ORACLE_LIKE and re.search(
        r"\bGROUP_CONCAT\s*\(", original_sql, re.IGNORECASE
    ):
        notes.append(
            "GROUP_CONCAT 已改写为 LISTAGG；MySQL 中的 ORDER BY 排序信息可能未保留，请复核。"
        )

    if source in {"postgres", "mysql"} and target in ORACLE_LIKE and re.search(
        r"(->>|->|\bJSON_(?:EXTRACT|VALUE|QUERY|OBJECT|ARRAY))",
        original_sql,
        re.IGNORECASE,
    ):
        notes.append(
            "检测到 JSON 操作符或函数，Oracle/达梦的 JSON 函数名和路径语法可能不同，请人工复核。"
        )

    if source == "postgres" and target == "mysql" and re.search(
        r"\bILIKE\b", original_sql, re.IGNORECASE
    ):
        notes.append("已把 PostgreSQL 的 ILIKE 改写为目标库的大小写不敏感写法。")

    if source == "mysql" and target != "mysql" and re.search(
        r"\bON\s+DUPLICATE\s+KEY\s+UPDATE\b", original_sql, re.IGNORECASE
    ):
        notes.append(
            "检测到 MySQL 的 ON DUPLICATE KEY UPDATE，目标库通常没有等价写法；"
            "请改用 MERGE、ON CONFLICT 等目标库语法。"
        )

    if source == "mysql" and target != "mysql" and re.search(
        r"\b(INSERT\s+IGNORE|REPLACE\s+INTO)\b", original_sql, re.IGNORECASE
    ):
        notes.append(
            "检测到 MySQL 的 INSERT IGNORE / REPLACE INTO，目标数据库可能不支持，请人工改写。"
        )

    if source == "postgres" and target != "postgres" and re.search(
        r"\bON\s+CONFLICT\b", original_sql, re.IGNORECASE
    ):
        notes.append(
            "检测到 PostgreSQL 的 ON CONFLICT，目标库的 upsert 语法不同；请人工复核。"
        )


def _parse_error(exc: ParseError | TokenError) -> ConversionError:
    error: dict[str, Any] = {}
    errors = getattr(exc, "errors", None)
    if errors:
        error = errors[0]

    line = error.get("line")
    column = error.get("col")
    detail = error.get("description") or str(exc).splitlines()[0]
    location = f"第 {line} 行第 {column} 列" if line else "输入内容"
    return ConversionError(
        f"无法解析{location}的 SQL，请检查语法或切换源数据库。",
        code="PARSE_ERROR",
        hint=str(detail).strip(),
        line=line,
        column=column,
    )


def _validate_request(sql: str, source: str, target: str) -> None:
    if source not in DIALECTS:
        raise ConversionError("不支持的源数据库。", code="UNSUPPORTED_DIALECT")
    if target not in DIALECTS:
        raise ConversionError("不支持的目标数据库。", code="UNSUPPORTED_DIALECT")
    if not sql or not sql.strip():
        raise ConversionError("请输入需要转换的 SQL。", code="EMPTY_SQL")
    if len(sql) > MAX_INPUT_LENGTH:
        raise ConversionError(
            f"SQL 长度超过 {MAX_INPUT_LENGTH:,} 个字符，请拆分后再转换。",
            code="SQL_TOO_LONG",
        )


def convert_sql(
    sql: str,
    source: str,
    target: str,
    *,
    pretty: bool = True,
) -> ConversionResult:
    """Convert SQL text from one supported dialect to another."""
    _validate_request(sql, source, target)
    started = time.perf_counter()
    notes: list[str] = []
    working_sql = sql

    if source == "db2":
        working_sql, db2_notes = _preprocess_db2(working_sql)
        notes.extend(db2_notes)

    try:
        statements = [
            statement
            for statement in sqlglot.parse(working_sql, read=source)
            if statement is not None
        ]
    except (ParseError, TokenError) as exc:
        raise _parse_error(exc) from exc
    except SqlglotError as exc:
        raise ConversionError(
            "SQL 解析失败，请检查输入语法。",
            code="PARSE_ERROR",
            hint=str(exc).splitlines()[0],
        ) from exc

    if not statements:
        raise ConversionError("没有找到可转换的 SQL 语句。", code="EMPTY_SQL")
    if len(statements) > MAX_STATEMENTS:
        raise ConversionError(
            f"单次最多转换 {MAX_STATEMENTS} 条语句，请拆分后再试。",
            code="TOO_MANY_STATEMENTS",
        )

    rendered: list[str] = []
    rownum_converted = 0
    identity_converted = 0
    serial_converted = 0
    properties_removed = 0
    column_comments_removed = 0
    listagg_converted = 0

    for statement in statements:
        _normalise_functions(statement)
        _normalise_integer_types(statement, target)
        identity_converted += _normalise_identity_columns(statement, target)
        serial_converted += _normalise_serial_columns(statement, target)
        listagg_converted += _normalise_listagg(statement, target)
        properties_removed += _strip_mysql_table_properties(statement, target)
        column_comments_removed += _strip_column_comments(statement, target)
        _normalise_dummy_tables(statement, target)

        if source in ORACLE_LIKE and target not in ORACLE_LIKE:
            rownum_converted += _replace_simple_rownum(statement)

        if target in ORACLE_LIKE:
            _rewrite_concat(statement)
            _add_dual(statement)

        try:
            generated = statement.sql(dialect=target, pretty=pretty).strip()
        except UnsupportedError as exc:
            raise ConversionError(
                f"该 SQL 含有无法转换为 {DIALECTS[target]['label']} 的语法，请人工处理。",
                code="UNSUPPORTED_SYNTAX",
                hint=str(exc).splitlines()[0],
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.exception("Unexpected SQL generation failure")
            raise ConversionError(
                "生成目标 SQL 时发生错误，请检查输入后重试。",
                code="GENERATION_ERROR",
                hint=str(exc).splitlines()[0],
            ) from exc

        if generated:
            rendered.append(generated)

    if not rendered:
        raise ConversionError("转换结果为空，请检查输入的 SQL。", code="EMPTY_RESULT")

    if rownum_converted:
        notes.append(
            f"已将 {rownum_converted} 处简单的 ROWNUM <= n 改写为目标数据库的分页语法；"
            "ROWNUM 与 ORDER BY 的执行顺序可能与原 SQL 不同，请复核结果。"
        )

    if identity_converted:
        if target == "mysql":
            notes.append(
                "已将 GENERATED AS IDENTITY 改写为 MySQL AUTO_INCREMENT；"
                "非整数类型已调整为 BIGINT。"
            )
        elif target == "dameng":
            notes.append(
                "已将 GENERATED AS IDENTITY 改写为达梦 IDENTITY(1,1)；"
                "非整数类型已调整为 BIGINT。"
            )
        else:
            notes.append(
                "identity 列的非整数类型已调整为 BIGINT，以符合目标数据库要求。"
            )

    if serial_converted:
        notes.append(
            f"已将 {serial_converted} 个 PostgreSQL SERIAL 列改写为目标库的自增语法。"
        )

    if listagg_converted:
        target_function = "GROUP_CONCAT" if target == "mysql" else "STRING_AGG"
        notes.append(
            f"已将 {listagg_converted} 处 LISTAGG 改写为 {target_function}；请确认分隔符和排序。"
        )

    if properties_removed:
        notes.append(
            "已移除 MySQL 的存储引擎、字符集、排序规则或表注释等表级选项；"
            "表注释请改用目标库的 COMMENT ON TABLE 语法。"
        )

    if column_comments_removed:
        notes.append(
            "已移除 MySQL 的字段注释；请在目标库使用 COMMENT ON COLUMN 语法补充。"
        )

    _add_notes_for_known_differences(
        notes, sql, source, target, rownum_converted
    )

    if not pretty:
        notes.append("已关闭美化格式，输出保留紧凑写法。")

    notes = list(dict.fromkeys(notes))

    output_sql = ";\n\n".join(rendered)
    elapsed_ms = max(1, round((time.perf_counter() - started) * 1000))
    result = ConversionResult(
        sql=output_sql,
        source=source,
        target=target,
        statement_count=len(rendered),
        elapsed_ms=elapsed_ms,
        pretty=pretty,
        input_chars=len(sql),
        notes=notes,
    )
    return result


def dialect_catalog() -> list[dict[str, str]]:
    return [{"key": key, **value} for key, value in DIALECTS.items()]
