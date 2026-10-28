# portion

Module: `portion`

Split a whole number of cents across people and format the shares for a receipt line.
This is a fictional kitchen-table helper. It does not move money.

## Constants

- `SEPARATOR = ", "` — characters placed between formatted shares

## Functions

### `split_cents(total: int, people: int) -> list[int]`

Divide `total` cents among `people` as evenly as possible.
The first `total % people` shares are one cent larger.
Use integer arithmetic only.
Raise `ValueError` when `total` is negative or `people` is less than 1.

Calls: none

Examples:

- `split_cents(100, 4) == [25, 25, 25, 25]`
- `split_cents(100, 3) == [34, 33, 33]`
- `split_cents(0, 2) == [0, 0]`

### `format_shares(shares: list[int]) -> str`

Render shares as a single line using `SEPARATOR`.
Raise `ValueError` when `shares` is empty or contains a negative number.

Calls: none

Examples:

- `format_shares([34, 33]) == "34, 33"`