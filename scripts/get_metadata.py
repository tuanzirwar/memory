#!/usr/bin/env python3
"""
feishu_get_message_metadata
获取飞书消息元数据

用法: python3 get_metadata.py <message_id> [tenant_key]
  message_id: 飞书消息 ID（om_xxx 格式）
  tenant_key: 可选，tenant key 不传则自动获取

环境变量（从 ~/.env 或 /home/gem/workspace/agent/.env 读取）:
  APP_ID     - 飞书应用 App ID（cli_xxx）
  APP_SECRET - 飞书应用 App Secret

示例: python3 get_metadata.py om_xxx
"""
import sys
import json
import os
import httpx
from pathlib import Path

def load_env():
    """从 .env 文件加载环境变量"""
    env_paths = [
        Path.home() / ".env",
        Path("/home/gem/workspace/agent/.env"),
        Path("/home/gem/workspace/agent/.env.local"),
    ]
    for env_path in env_paths:
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        if "=" in line:
                            key, val = line.split("=", 1)
                            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
            break

load_env()

async def get_tenant_access_token(app_id: str, app_secret: str) -> dict:
    """
    获取 tenant_access_token
    接口: https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=30.0
        )
        data = resp.json()
        
        if data.get("code") != 0:
            return {
                "success": False,
                "error": f"获取 tenant_access_token 失败: code={data.get('code')}, msg={data.get('msg')}"
            }
        
        return {
            "success": True,
            "tenant_access_token": data["tenant_access_token"],
            "expire": data.get("expire", 0)
        }

async def get_message_metadata(message_id: str, app_id: str, app_secret: str, tenant_key: str = None) -> dict:
    """
    获取飞书消息元数据
    自动获取 tenant_access_token，再调用消息接口
    """
    # 第一步：获取 tenant_access_token
    token_result = await get_tenant_access_token(app_id, app_secret)
    if not token_result["success"]:
        return {"error": token_result["error"]}
    
    token = token_result["tenant_access_token"]
    
    # 第二步：调用消息接口获取元数据
    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bearer {token}"}
        
        msg_resp = await client.get(
            f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
            headers=headers,
            timeout=30.0
        )
        data = msg_resp.json()
        
        if data.get("code") != 0:
            return {
                "error": f"获取消息失败: code={data.get('code')}, msg={data.get('msg')}",
                "hint": "可能原因：消息不存在、机器人无权查看、应用未开通消息读取权限"
            }
        
        items = data.get("data", {}).get("items", [])
        if not items:
            return {"error": "消息内容为空"}
        
        msg_data = items[0]
        
        return {
            "success": True,
            "message_id": message_id,
            "chat_id": msg_data.get("chat_id"),
            "chat_type": msg_data.get("chat_type"),
            "tenant_key": tenant_key,
            "sender": {
                "open_id": msg_data.get("sender", {}).get("sender_id", {}).get("open_id"),
                "user_id": msg_data.get("sender", {}).get("sender_id", {}).get("user_id"),
                "tenant_key": tenant_key
            },
            "full_data": msg_data,
            "token_expire": token_result["expire"]
        }

def main():
    app_id = os.environ.get("APP_ID")
    app_secret = os.environ.get("APP_SECRET")
    
    if len(sys.argv) < 2:
        print(json.dumps({
            "error": "参数不足",
            "usage": "python3 get_metadata.py <message_id> [tenant_key]",
            "example": "python3 get_metadata.py om_xxx",
            "hint": "确保 APP_ID 和 APP_SECRET 已配置在 ~/.env 或 /home/gem/workspace/agent/.env"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)
    
    if not app_id or not app_secret:
        print(json.dumps({
            "error": "缺少凭证",
            "hint": "请在 ~/.env 或 /home/gem/workspace/agent/.env 中配置 APP_ID 和 APP_SECRET"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)
    
    message_id = sys.argv[1]
    tenant_key = sys.argv[2] if len(sys.argv) > 2 else None
    
    import asyncio
    result = asyncio.run(get_message_metadata(message_id, app_id, app_secret, tenant_key))
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
