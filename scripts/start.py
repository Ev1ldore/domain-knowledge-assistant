"""Local launcher: hidden administrator password prompt, no secret files."""
import getpass
import os
from pathlib import Path
import subprocess
import sys
root=Path(__file__).resolve().parents[1]
env=os.environ.copy()
if not env.get('KB_ADMIN_PASSWORD'):
    password=getpass.getpass('为本次服务设置管理员密码（不写入文件）: ')
    if not password:raise SystemExit('密码不能为空')
    env['KB_ADMIN_PASSWORD']=password
raise SystemExit(subprocess.call([sys.executable,str(root/'app.py')],cwd=root,env=env))
