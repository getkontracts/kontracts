"""Fail unless installed runtime packages match the reviewed deployment pins."""
from importlib.metadata import PackageNotFoundError,version
from pathlib import Path
import sys
errors=[]
for line in (Path(__file__).resolve().parents[1]/'requirements.txt').read_text().splitlines():
    line=line.strip()
    if not line or line.startswith('#'):continue
    requirement,expected=line.split('==');name=requirement.split('[')[0]
    try:actual=version(name)
    except PackageNotFoundError:actual='NOT INSTALLED'
    if actual!=expected:errors.append(f'{name}: expected {expected}; installed {actual}')
if errors:
    print('Deployment dependency gate FAILED. Local evaluated versions are not production approval.')
    print('\n'.join(errors));sys.exit(1)
print('Deployment pins match. Still run pip check, full tests and an online vulnerability audit.')
