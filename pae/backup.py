#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenGloss 学习数据备份 —— 生成**事务自洽的单文件快照**。

为什么不能直接 copy pae.db：
    引擎用 SQLite 的 WAL 模式，新写入先落在 pae.db-wal，稍后才并入主文件。
    直接拷主文件会丢掉最后一批写入；极端情况下备份出来是**有表无数据的空库**，
    而且它的 integrity_check 依然报 ok —— 好坏肉眼分辨不出。
    实测（引擎运行中写入 8 条后）：只拷主文件 -> events 0 条；本脚本 -> 8 条。

为什么用 VACUUM INTO：
    它是单条 SQL，产出一个**已整理、自洽、单文件**的副本，且**引擎运行时也能安全执行**
    （内部走读事务，不会与写入冲突，也不需要停引擎、不需要碰 -wal/-shm）。

用法：直接双击 backup.bat，或 python backup.py
"""
import glob
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "pae.db")
DST_DIR = os.path.join(HERE, "backups")
KEEP = 10


def _ro(path):
    """以只读方式打开（避免备份动作本身给源库建出 -wal/-shm）。"""
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)


def main():
    if not os.path.exists(SRC):
        print("[PAE] pae.db not found - nothing to back up")
        return 1

    os.makedirs(DST_DIR, exist_ok=True)
    out = os.path.join(DST_DIR, "pae-%s.db" % time.strftime("%Y%m%d-%H%M%S"))

    # 1) 读出源库事件数，并做自洽快照
    src = _ro(SRC)
    try:
        n_src = src.execute("select count(*) from events").fetchone()[0]
        src.execute("VACUUM INTO ?", (out,))
    finally:
        src.close()

    # 2) 校验备份本身（这一步是关键：旧做法永远通不过这里）
    bak = _ro(out)
    try:
        integrity = bak.execute("pragma integrity_check").fetchone()[0]
        n_bak = bak.execute("select count(*) from events").fetchone()[0]
    finally:
        bak.close()

    ok = (integrity == "ok" and n_bak == n_src)
    print("[PAE] backup %s: %s | events %d -> %d | integrity=%s"
          % ("OK" if ok else "MISMATCH", os.path.basename(out), n_src, n_bak, integrity))
    if not ok:
        print("[PAE] backup does not match source - file kept for inspection")
        return 2

    # 3) 只保留最近 KEEP 份
    files = sorted(glob.glob(os.path.join(DST_DIR, "pae-*.db")), key=os.path.getmtime, reverse=True)
    for old in files[KEEP:]:
        try:
            os.remove(old)
        except OSError:
            pass
    print("[PAE] backups/ now holds %d copy(ies), keeping latest %d" % (min(len(files), KEEP), KEEP))
    return 0


if __name__ == "__main__":
    sys.exit(main())
