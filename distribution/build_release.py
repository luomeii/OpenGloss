"""Build the offline Windows x64 asset from locked PUBLIC inputs only."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile

INSTALL_FILES = ('Install.cmd', 'distribution/install.ps1',
                 'distribution/inputs.lock.json', '安装说明.md')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def member_path(name):
    value = name.rstrip('/')
    parts = value.split('/')
    if (not value or '\\' in value or ':' in value
            or any(p in ('', '.', '..') or p != p.rstrip(' .') for p in parts)):
        raise ValueError('invalid archive path: ' + name)
    return PurePosixPath(value)


def locked_input(cache, item):
    name = item['filename']
    if member_path(name).name != name:
        raise ValueError('invalid input filename')
    path = cache / name
    if (not path.is_file() or path.is_symlink()
            or path.stat().st_size != item['size']
            or sha256(path.read_bytes()) != item['sha256']):
        raise ValueError('public input mismatch: ' + name)
    return path


def unpack(archive_path, destination, wheel=False):
    with zipfile.ZipFile(archive_path) as archive:
        seen = set()
        for item in archive.infolist():
            relative = member_path(item.filename)
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('archive symlink: ' + item.filename)
            if wheel and relative.parts[0].endswith('.data'):
                if len(relative.parts) < 3 or relative.parts[1] not in ('purelib', 'platlib'):
                    raise ValueError('unsupported wheel data: ' + item.filename)
                relative = PurePosixPath(*relative.parts[2:])
            normalized = relative.as_posix().casefold()
            if normalized in seen:
                raise ValueError('archive duplicate: ' + item.filename)
            seen.add(normalized)
            target = destination.joinpath(*relative.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if target.exists():
                raise ValueError('archive collision: ' + item.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(item))


def build(source, cache, output, lock):
    source, cache, output = Path(source), Path(cache), Path(output)
    if output.exists():
        raise ValueError('output already exists; choose a new output filename')
    for name, expected in lock['baseline_files'].items():
        relative = member_path(name)
        path = source.joinpath(*relative.parts)
        if (not path.is_file() or path.is_symlink()
                or sha256(path.read_bytes()) != expected):
            raise ValueError('original baseline mismatch: ' + name)
    runtime = locked_input(cache, lock['runtime'])
    dictionary = locked_input(cache, lock['dictionary'])
    wheels = [locked_input(cache, item) for item in lock['wheels']]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='build-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'package'
        stage.mkdir()
        for name in (*lock['baseline_files'], *INSTALL_FILES):
            relative = member_path(name)
            original = source.joinpath(*relative.parts)
            if not original.is_file() or original.is_symlink():
                raise ValueError('missing package file: ' + name)
            target = stage.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        unpack(runtime, stage / 'pae')
        pth = stage / 'pae/python312._pth'
        if not pth.is_file():
            raise ValueError('expected Python 3.12 embeddable runtime')
        # This generated runtime file is not part of the original application.
        # All search locations are relative, isolated from global Python installs.
        pth.write_text('python312.zip\n.\nLib/site-packages\n', encoding='ascii')
        site = stage / 'pae/Lib/site-packages'
        for wheel in wheels:
            unpack(wheel, site, wheel=True)
        shutil.copyfile(dictionary, stage / 'resources/ECDICT/dict.sqlite')
        files = {p.relative_to(stage).as_posix(): sha256(p.read_bytes())
                 for p in sorted(stage.rglob('*')) if p.is_file()}
        manifest = {'schema': 1, 'baseline_commit': lock['baseline_commit'], 'files': files}
        (stage / 'INSTALL-MANIFEST.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        packed = Path(temporary) / 'asset.zip'
        with zipfile.ZipFile(packed, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(stage.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(stage).as_posix())
        packed.replace(output)
    return {'files': len(files) + 1, 'sha256': sha256(output.read_bytes()), 'size': output.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True,
                        help='directory containing all files named in inputs.lock.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    lock = json.loads((source / 'distribution/inputs.lock.json').read_text(encoding='utf-8'))
    print(json.dumps(build(source, args.inputs, args.output, lock)))


if __name__ == '__main__':
    main()
