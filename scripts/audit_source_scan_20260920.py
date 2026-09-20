"""Read every first-party source file and save a reproducible syntax inventory."""
import ast
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {'.py', '.js', '.html', '.css', '.bat', '.spec'}


def main():
    files = [p for folder in ('autodub', 'gui', 'ui', 'scripts', 'tests')
             for p in (ROOT / folder).rglob('*') if p.suffix in SUFFIXES]
    files += [p for p in ROOT.iterdir() if p.is_file() and p.suffix in SUFFIXES]
    files = sorted(p for p in files if not p.name.startswith(('audit_repro_', 'audit_ui_repro_', 'audit_source_scan_')))
    rows = []
    for path in files:
        raw = path.read_bytes()
        text = raw.decode('utf-8-sig')
        item = {'path': path.relative_to(ROOT).as_posix(), 'lines': len(text.splitlines()),
                'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                'read': True, 'validation': 'text read (no dedicated parser)'}
        if path.suffix in {'.py', '.spec'}:
            try:
                tree = ast.parse(text)
                item['validation'] = 'Python AST parsed'
                item['functions'] = [{'name': node.name, 'line': node.lineno,
                                      'end_line': node.end_lineno}
                                     for node in ast.walk(tree)
                                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
            except SyntaxError as exc:
                item['validation'] = f'SYNTAX ERROR: {exc}'
        elif path.suffix == '.js':
            checked = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
            item['validation'] = 'Node syntax passed' if checked.returncode == 0 else checked.stderr
        rows.append(item)
    output = {'scope': 'First-party source, launchers and tests; excludes dependencies, model caches, media and audit scripts.',
              'files': len(rows), 'lines': sum(row['lines'] for row in rows),
              'extensions': dict(Counter(Path(row['path']).suffix for row in rows)), 'inventory': rows}
    destination = ROOT / '_tmp' / 'audit_source_inventory_20260920.json'
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    lint = subprocess.run([sys.executable, '-m', 'ruff', 'check', '--output-format', 'json',
                           *[str(p) for p in files if p.suffix == '.py']],
                          capture_output=True, cwd=ROOT)
    warnings = json.loads(lint.stdout.decode('utf-8'))
    (ROOT / '_tmp' / 'audit_ruff_all_20260920.json').write_text(
        json.dumps(warnings, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in output.items() if k != 'inventory'}, indent=2))
    print('Ruff warnings:', len(warnings), dict(Counter(row['code'] for row in warnings)))
    print('Syntax errors:', sum('ERROR' in row['validation'] for row in rows))


if __name__ == '__main__':
    main()
