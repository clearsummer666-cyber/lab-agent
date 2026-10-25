"""eval 包：评测框架。

四个文件，各管一件事：
    core.py      数据结构（Task / RunResult / CheckResult / Grade）
    tasks.py     黄金集（题目）
    graders.py   评分器 + 护栏
    harness.py   跑 + 记录 + 汇总 + 稳定性
    sut.py       被测对象（这一课是假 Agent）
"""
