"""`python -m eia ...` 等同于 `eia ...`。定时任务用 `pythonw -m eia run ...`，不弹出命令行窗口。"""

from eia.cli import main

main()
