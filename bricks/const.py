# -*- coding: utf-8 -*-
# @Time    : 2023-11-15 17:42
# @Author  : Kem
# @Desc    :
import hashlib
import uuid

# 当前 机器 ID
MACHINE_ID = hashlib.sha256(uuid.UUID(int=uuid.getnode()).hex[-12:].encode()).hexdigest()

# 当前框架版本
VERSION = "0.0.1"

# 事件类型
ERROR_OCCURRED = 'ERROR_OCCURRED'
BEFORE_START = 'BEFORE_START'
BEFORE_CLOSE = 'BEFORE_CLOSE'
ON_CONSUME = 'ON_CONSUME'
ON_PARSING = 'ON_PARSING'
BEFORE_GET_SEEDS = "BEFORE_GET_SEEDS"
AFTER_GET_SEEDS = "AFTER_GET_SEEDS"
BEFORE_RETRY = "BEFORE_RETRY"
AFTER_RETRY = "AFTER_RETRY"
BEFORE_REQUEST = "BEFORE_REQUEST"
AFTER_REQUEST = "AFTER_REQUEST"
BEFORE_PIPELINE = "BEFORE_PIPELINE"
AFTER_PIPELINE = "AFTER_PIPELINE"
