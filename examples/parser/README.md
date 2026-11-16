# Parser example catalog

This directory contains a standalone question catalog for small parser
functions such as `parse_integer(text)` and `parse_boolean(text)`. It is a
question catalog only, not a parser implementation.

The catalog covers common parser design decisions:

- invalid input handling (`ValueError` or `None`)
- empty input handling
- leading and trailing whitespace
- integer sign and accepted integer formats
- boolean letter case and accepted boolean words

Check the catalog with:

```bash
specfreeze catalog check examples/parser/catalog.yaml
```
