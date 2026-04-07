"""
统一数据块结构：DataBlock
所有数据采集函数返回此对象，替代裸 list/dict/None。
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DataBlock:
    name: str                        # 模块名，如 "fund_flow_rank"
    data: Any = None                 # 实际数据（list / dict / DataFrame）
    status: str = "success"          # success / empty / fail
    msg: str = ""                    # 失败原因或备注
    latency: float = 0.0             # 采集耗时（秒）
    source: str = ""                 # 数据来源标识

    @property
    def ok(self) -> bool:
        return self.status == "success" and bool(
            self.data if not hasattr(self.data, "empty") else not self.data.empty
        )

    def __repr__(self):
        return (f"DataBlock({self.name!r}, status={self.status!r}, "
                f"rows={len(self.data) if self.data is not None else 0}, "
                f"latency={self.latency:.2f}s)")


def ok(name: str, data, latency: float = 0.0, source: str = "") -> DataBlock:
    return DataBlock(name=name, data=data, status="success",
                     latency=latency, source=source)


def empty(name: str, msg: str = "", latency: float = 0.0) -> DataBlock:
    return DataBlock(name=name, data=None, status="empty",
                     msg=msg, latency=latency)


def fail(name: str, error, latency: float = 0.0) -> DataBlock:
    return DataBlock(name=name, data=None, status="fail",
                     msg=str(error)[:200], latency=latency)
