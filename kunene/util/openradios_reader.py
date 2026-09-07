
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import logging
logger = logging.getLogger(__name__)


@dataclass
class _ParameterBlock:
    scope: str           # GLOBAL or LOCAL
    param_type: str      # REAL, INTEGER, REAL_EXPR, INT_EXPR, TEXT
    param_id: int
    title: str
    name: str
    value: Any           # float, int, or str depending on param_type
    header_line_idx: int
    name_line_idx: int
    value_line_idx: Optional[int] = None    # TEXT only: line holding the text value
    text_length: Optional[int] = None       # TEXT only: Length field (0 = full line)
    expr_line_indices: List[int] = field(default_factory=list)  # REAL_EXPR/INT_EXPR continuation lines


class OpenRadiosKeywordReader:
    """Context-manager reader for Radioss keyword files.

    Supports reading and modifying /PARAMETER keywords (REAL, INTEGER,
    REAL_EXPR, INT_EXPR, TEXT) in GLOBAL and LOCAL scope.

    Usage::

        with OpenRadiosKeywordReader(input_file) as okr:
            params = okr.parameters()
            for name, (ptype, value) in params.items():
                print(f"  {name} ({ptype}): {value}")

            okr.set_parameters({"Term": 0.5, "States": 100})
            okr.write(output_file)
    """

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self._lines: List[str] = []
        self._blocks: List[_ParameterBlock] = []

    # ------------------------------------------------------------------
    # context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "OpenRadiosKeywordReader":
        self._read()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return False

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def parameters(self) -> Dict[str, Tuple[str, Any]]:
        """Return all parameters as ``{name: (type, value)}``.

        *type* is one of ``'REAL'``, ``'INTEGER'``, ``'REAL_EXPR'``,
        ``'INT_EXPR'``, ``'TEXT'``.
        *value* is ``float``, ``int``, or ``str`` accordingly.
        """
        return {b.name: (b.param_type, b.value) for b in self._blocks}

    def set_parameters(self, params_dict: Dict[str, Any]) -> None:
        """Update parameter values in-memory.

        Name matching is case-insensitive.  Unrecognised names are silently
        ignored.

        Args:
            params_dict: ``{name: new_value}``
        """
        name_map: Dict[str, _ParameterBlock] = {
            b.name.upper(): b for b in self._blocks
        }

        for name, new_value in params_dict.items():
            block = name_map.get(name.upper())
            if block is None:
                #logger.warning("Parameter '%s' not found in '%s'.", name, self.file_path)
                continue

            logger.info("Setting parameter '%s' to %s.", name, str(new_value) )
            block.value = new_value

            if block.param_type == "TEXT":
                eol = self._eol(self._lines[block.value_line_idx])
                self._lines[block.value_line_idx] = str(new_value) + eol
            elif block.param_type in ("REAL_EXPR", "INT_EXPR"):
                # Replace first expression line; remove any continuation lines.
                old_line = self._lines[block.name_line_idx]
                eol = self._eol(old_line)
                m = re.match(r"^(\s*\S+\s+)", old_line)
                prefix = m.group(1) if m else f"{block.name}  "
                self._lines[block.name_line_idx] = prefix + str(new_value) + eol
                # Clear continuation lines (replace with empty lines so indices stay valid)
                for idx in block.expr_line_indices:
                    self._lines[idx] = self._eol(self._lines[idx]) or "\n"
                block.expr_line_indices = []
            else:
                old_line = self._lines[block.name_line_idx]
                eol = self._eol(old_line)
                m = re.match(r"^(\s*\S+\s+)", old_line)
                if m:
                    self._lines[block.name_line_idx] = m.group(1) + str(new_value) + eol
                else:
                    self._lines[block.name_line_idx] = f"{block.name}  {new_value}{eol}"

    def write(self, output_file: str) -> None:
        """Write the (possibly modified) content to *output_file*."""
        with open(output_file, "w") as f:
            f.writelines(self._lines)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _read(self) -> None:
        with open(self.file_path, "r") as f:
            self._lines = f.read().splitlines(keepends=True)
        self._parse()

    def _parse(self) -> None:
        """Scan lines and build a _ParameterBlock for every /PARAMETER entry."""
        self._blocks = []
        i = 0
        n = len(self._lines)

        header_re = re.compile(
            r"^/PARAMETER/(GLOBAL|LOCAL)/(REAL_EXPR|INT_EXPR|REAL|INTEGER|TEXT)/(\d+)",
            re.IGNORECASE,
        )

        while i < n:
            m = header_re.match(self._lines[i].strip())
            if m:
                scope = m.group(1).upper()
                param_type = m.group(2).upper()
                param_id = int(m.group(3))
                header_line_idx = i

                i += 1
                if i >= n:
                    break
                title = self._lines[i].strip()

                i += 1
                if i >= n:
                    break
                name_line_idx = i
                parts = self._lines[i].split()

                if param_type == "TEXT":
                    name = parts[0] if parts else ""
                    # parts[1] is the optional Length field (0 = full line)
                    text_length = int(parts[1]) if len(parts) >= 2 else 0
                    i += 1
                    if i >= n:
                        break
                    value_line_idx = i
                    # Preserve raw content (including significant leading whitespace)
                    raw_line = self._lines[i]
                    value = raw_line.rstrip("\r\n")
                    self._blocks.append(
                        _ParameterBlock(
                            scope=scope,
                            param_type=param_type,
                            param_id=param_id,
                            title=title,
                            name=name,
                            value=value,
                            header_line_idx=header_line_idx,
                            name_line_idx=name_line_idx,
                            value_line_idx=value_line_idx,
                            text_length=text_length,
                        )
                    )

                elif param_type in ("REAL_EXPR", "INT_EXPR"):
                    name = parts[0] if parts else ""
                    # First expression portion is on the same line as ParName
                    after_name = re.match(r"^\s*\S+\s+(.*?)\s*$", self._lines[i])
                    first_expr = after_name.group(1) if after_name else (parts[1] if len(parts) >= 2 else "")
                    # Collect up to 9 continuation lines (max 10 total per docs)
                    expr_line_indices: List[int] = []
                    j = i + 1
                    continuation_re = re.compile(r"^/|^#|^\s*$")
                    while j < n and len(expr_line_indices) < 9:
                        line = self._lines[j]
                        # Stop at the next keyword, comment, or blank line
                        if continuation_re.match(line):
                            break
                        expr_line_indices.append(j)
                        j += 1
                    # Build full expression string
                    expr_parts = [first_expr] + [self._lines[k].rstrip("\r\n") for k in expr_line_indices]
                    value = "\n".join(expr_parts)
                    self._blocks.append(
                        _ParameterBlock(
                            scope=scope,
                            param_type=param_type,
                            param_id=param_id,
                            title=title,
                            name=name,
                            value=value,
                            header_line_idx=header_line_idx,
                            name_line_idx=name_line_idx,
                            expr_line_indices=expr_line_indices,
                        )
                    )

                else:  # REAL or INTEGER
                    name = parts[0] if parts else ""
                    if len(parts) >= 2:
                        raw = parts[1]
                        if param_type == "INTEGER":
                            try:
                                value: Any = int(raw)
                            except ValueError:
                                value = raw
                        else:  # REAL
                            try:
                                value = float(raw)
                            except ValueError:
                                value = raw
                    else:
                        value = None

                    self._blocks.append(
                        _ParameterBlock(
                            scope=scope,
                            param_type=param_type,
                            param_id=param_id,
                            title=title,
                            name=name,
                            value=value,
                            header_line_idx=header_line_idx,
                            name_line_idx=name_line_idx,
                        )
                    )

            i += 1

    @staticmethod
    def _eol(line: str) -> str:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
        return ""
