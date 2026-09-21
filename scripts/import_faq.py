"""Repeatable FAQ import through the authenticated application API."""
import argparse
import getpass
import os
from pathlib import Path
import httpx
p=argparse.ArgumentParser();p.add_argument('file',type=Path);p.add_argument('--url',default='http://127.0.0.1:8080');a=p.parse_args()
password=os.getenv('KB_ADMIN_PASSWORD') or getpass.getpass('管理员密码: ')
with httpx.Client(base_url=a.url,timeout=60,trust_env=False) as client:
    r=client.post('/api/kb/login',json={'username':os.getenv('KB_ADMIN_USERNAME','admin'),'password':password});r.raise_for_status()
    token=r.json()['token']
    try:
        with a.file.open('rb') as f:
            r=client.post('/api/kb/faq/import',headers={'Authorization':'Bearer '+token},files={'file':(a.file.name,f)})
        r.raise_for_status();print(r.json())
    finally:client.post('/api/kb/logout',headers={'Authorization':'Bearer '+token})
