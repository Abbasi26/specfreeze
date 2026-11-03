# Demo screenshot

![specfreeze demo output](demo.png)

`demo.png` (1600x900) is the unedited terminal output of a real demo run, rendered to an image on 2026-11-03. All hosts, users, devices and files in it are fictional sample data that ships with this repository. No network service or real system was involved.

Setup: python -m pip install -e ".[dev]".

Commands, in the order shown in the image:

```text
$ python -m specfreeze demo --out .specfreeze/demo

$ specfreeze verify .specfreeze/demo/portion.py --contract .specfreeze/demo
```

Pipes such as `head`, `grep` or `sed` in the commands above only shorten the output so it fits on one screen. They are part of the shown command.
