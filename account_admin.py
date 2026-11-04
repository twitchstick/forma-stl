"""Local terminal-only recovery for the single Forma owner account."""
import argparse
import getpass
import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from werkzeug.security import generate_password_hash


def main():
    parser=argparse.ArgumentParser(description='Recover the Forma owner account from an interactive server terminal.')
    parser.add_argument('action',choices=['reset-password']);parser.add_argument('--username',help='Optional replacement username')
    args=parser.parse_args()
    if not sys.stdin.isatty():parser.error('Run interactively: docker exec -it forma python account_admin.py reset-password')
    if args.username and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,79}',args.username):parser.error('Choose a valid username (letters, numbers, dots, underscores, @ or hyphens).')
    path=Path(os.environ.get('FORMA_DATA','/data'))/'forma.sqlite3'
    if not path.is_file():parser.error('Forma database not found. Start Forma once before recovery.')
    password=getpass.getpass('New password (12–256 characters): ');confirm=getpass.getpass('Confirm new password: ')
    if not 12<=len(password)<=256 or password!=confirm:parser.error('Passwords must match and contain 12–256 characters.')
    hashed=generate_password_hash(password,method="pbkdf2:sha256:1000000")
    with closing(sqlite3.connect(path,timeout=30)) as c:
        with c:
            old=c.execute('SELECT username FROM account_profile WHERE id=1').fetchone()
            if not old:parser.error('Owner account not found. Start the updated Forma container first.')
            c.execute('UPDATE account_profile SET username=?,password_hash=?,auth_version=auth_version+1,updated_at=? WHERE id=1',(args.username or old[0],hashed,datetime.now(timezone.utc).isoformat()))
            for table in ('account_sessions','slicer_downloads','companion_codes'):c.execute('DELETE FROM '+table)
            c.execute('UPDATE companion_devices SET revoked=1 WHERE revoked=0')
    print('Password reset. Sign in to Forma again; previous browser sessions and companion pairings are revoked.')

if __name__=='__main__':main()
