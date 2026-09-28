#!/usr/bin/env python3
"""Read-only SupoClip production health, job, and OpenRouter balance snapshot.

Run from this checkout using Python 3. No purchases, job submissions, or service
changes. API keys are read on the production host and never returned.
VideoScale remaining bandwidth must be checked in its authenticated dashboard.
"""
import json
import shlex
import subprocess
import urllib.request

REMOTE = r'''
import asyncio, json, subprocess, urllib.request
from datetime import datetime, timezone
from sqlalchemy import text
from src.config import get_config
from src.database import get_session_maker
from src.runtime_settings import load_runtime_settings_cache
async def main():
 result={'checked_at':datetime.now(timezone.utc).isoformat()}
 async with get_session_maker()() as db:
  await load_runtime_settings_cache(db)
  rows=await db.execute(text("""
   SELECT id::text,status,progress,error_code,created_at,updated_at
   FROM tasks WHERE created_at > NOW()-INTERVAL '48 hours'
      OR status IN ('queued','processing')
   ORDER BY created_at DESC LIMIT 100
  """))
  result['tasks']=[dict(row) for row in rows.mappings()]
 cfg=get_config()
 result['configuration']={'downloader':cfg.youtube_download_provider,'model':cfg.llm,
   'fallback':getattr(cfg,'openrouter_fallback_model',None)}
 try:
  with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=15) as response:
   result['backend_health']=json.load(response)
 except Exception as error: result['backend_health']={'error_type':type(error).__name__}
 if cfg.openrouter_api_key:
  result['openrouter']={}
  for route in ['key','credits']:
   request=urllib.request.Request('https://openrouter.ai/api/v1/'+route,
     headers={'Authorization':'Bearer '+cfg.openrouter_api_key})
   try:
    with urllib.request.urlopen(request,timeout=20) as response:data=json.load(response)['data']
    fields=['limit','limit_remaining','usage','is_free_tier'] if route=='key' else ['total_credits','total_usage']
    result['openrouter'][route]={k:data.get(k) for k in fields}
   except Exception as error:result['openrouter'][route]={'error_type':type(error).__name__}
 print(json.dumps(result,default=str))
asyncio.run(main())
'''

def main():
    snapshot={}
    try:
        with urllib.request.urlopen('https://www.supoclip.com/',timeout=20) as response:
            snapshot['public_site_status']=response.status
    except Exception as error:
        snapshot['public_site_error']=type(error).__name__
    command='docker exec supoclip-backend /app/.venv/bin/python -c '+shlex.quote(REMOTE)
    try:
        response=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','hetzner',command],
            check=True,capture_output=True,text=True,timeout=90)
        snapshot['production']=json.loads(response.stdout)
    except Exception as error:
        snapshot['production_error']=type(error).__name__
    try:
        response=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','hetzner',
            'docker ps -a --filter name=supoclip --format '+shlex.quote('{{.Names}}: {{.Status}}')],
            check=True,capture_output=True,text=True,timeout=25)
        snapshot['containers']=response.stdout.strip().splitlines()
    except Exception as error:
        snapshot['container_check_error']=type(error).__name__
    print(json.dumps(snapshot,indent=2))

if __name__=='__main__':main()
