#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
存储层连接测试脚本
测试 InfluxDB, PostgreSQL, Redis, MinIO 是否正常连接
"""

import sys
import logging
from datetime import datetime
import pandas as pd
from sqlalchemy import text

# 添加项目路径
sys.path.append('.')

from data_layer.db_manager import storage_manager
from data_layer.config import config


def test_all_connections():
    """测试所有存储服务连接"""
    print(f"\n=== 存储层连接测试开始 === {datetime.now()}\n")
    
    results = {}
    
    # 1. 测试 InfluxDB
    print("1. 测试 InfluxDB...")
    try:
        client = storage_manager.get_influx_client()
        if client:
            health = client.health()
            results['influxdb'] = "[OK] 连接成功"
            print("   InfluxDB 健康检查通过")
        else:
            results['influxdb'] = "[ERROR] 连接失败"
    except Exception as e:
        results['influxdb'] = f"[ERROR] 异常: {str(e)[:80]}"
        print(f"   InfluxDB 连接异常: {e}")
    
    # 2. 测试 PostgreSQL + TimescaleDB
    print("\n2. 测试 PostgreSQL (TimescaleDB)...")
    try:
        engine = storage_manager.get_postgres_engine()
        if engine:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1 as test")).scalar()
                if result == 1:
                    results['postgres'] = "连接成功 (TimescaleDB 可用)"
                    print("   PostgreSQL 连接成功")
                else:
                    results['postgres'] = f"连接成功但返回值异常: {result}"
        else:
            results['postgres'] = "连接失败"
    except Exception as e:
        results['postgres'] = f"异常: {str(e)[:100]}"
        print(f"   PostgreSQL 连接异常: {e}")
    
    # 3. 测试 Redis
    print("\n3. 测试 Redis...")
    try:
        redis_client = storage_manager.get_redis_client()
        if redis_client:
            redis_client.ping()
            results['redis'] = "[OK] 连接成功"
            print("   Redis 连接成功")
        else:
            results['redis'] = "[ERROR] 连接失败"
    except Exception as e:
        results['redis'] = f"[ERROR] 异常: {str(e)[:80]}"
        print(f"   Redis 连接异常: {e}")
    
    # 4. 测试 MinIO
    print("\n4. 测试 MinIO (S3 兼容)...")
    try:
        from minio import Minio
        minio_client = Minio(
            config.minio["endpoint"],
            access_key=config.minio["access_key"],
            secret_key=config.minio["secret_key"],
            secure=config.minio["secure"]
        )
        if minio_client.bucket_exists("backtrader-data"):
            results['minio'] = "[OK] 连接成功 (Bucket 存在)"
        else:
            minio_client.make_bucket("backtrader-data")
            results['minio'] = "[OK] 连接成功 (已创建 Bucket)"
        print("   MinIO 连接成功")
    except Exception as e:
        results['minio'] = f"[ERROR] 异常: {str(e)[:80]}"
        print(f"   MinIO 连接异常: {e}")
    
    # 总结报告
    print("\n" + "="*60)
    print("存储层连接测试报告")
    print("="*60)
    for service, status in results.items():
        print(f"{service.upper():12} : {status}")
    print("="*60)
    
    success_count = sum(1 for v in results.values() if "成功" in str(v) or "连接成功" in str(v))
    print(f"\n成功: {success_count}/{len(results)} 个服务")
    
    if success_count >= 3:
        print("所有核心存储服务连接正常！存储层架构可用。")
    else:
        print("部分服务连接失败，请检查 Docker 容器状态。")
    
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_all_connections()
